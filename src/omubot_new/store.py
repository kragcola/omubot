"""The one SQLite executor; stores digests and decisions, never conversation text."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import IO, Literal, TypeVar, cast
from zoneinfo import ZoneInfo

import portalocker
from pydantic import JsonValue, TypeAdapter, ValidationError

from .types import (
    ActionCall,
    BotContactInput,
    ContactAuthorityProof,
    Event,
    OperationError,
    QQDeliveryLimits,
    QQDeliverySnapshot,
    QQQuotaAttempt,
    QQQuotaProjection,
    QQScopeKey,
    QQStateRow,
    QQTransportEvidence,
    QQWriteSpec,
    Scope,
)

T = TypeVar("T")
_SCHEMA_VERSION = 49
_QQ_LEGACY_WRITES = ("message.reply", "message.sticker", "manage_group", "poke", "reaction")
_QQ_ACTION_COLUMNS = {
    "qq_account_id": "TEXT",
    "qq_cost": "INTEGER NOT NULL DEFAULT 0 CHECK(qq_cost IN (0,1))",
    "qq_committed_at": "REAL",
    "qq_budget_anchor": "REAL",
    "qq_settled_at": "REAL",
    "qq_governor_revision": "INTEGER",
    "qq_api": "TEXT NOT NULL DEFAULT ''",
    "qq_params_hash": "TEXT NOT NULL DEFAULT ''",
    "qq_review_ref": "INTEGER",
}


def request_digest(event: Event) -> str:
    """Canonical persisted request identity shared by request-bound owners."""
    # Preserve the pre-mention digest for plain-event reconnect replay.
    excluded: dict[str, bool | set[str]] = {}
    if event.scope.kind == "group":
        excluded["scope"] = {"kind"}
    if not event.reply_to and not event.mentioned and not event.mention_targets:
        excluded.update(dict.fromkeys(("message_id", "reply_to", "mentioned", "mention_targets"), True))
    serialized = event.model_dump_json(exclude=excluded)
    return hashlib.sha256(serialized.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ContactRequestRecord:
    contact: BotContactInput
    revision: int
    active: bool
    decision: str
    reason_code: str
    due_at: float | None
    decision_started_at: float | None
    send_started_at: float | None


def contact_input_digest(contact: BotContactInput) -> str:
    """The exact Bot-origin pins, without a fabricated human ingress source."""
    values = asdict(contact)
    values["scope"] = contact.scope.model_dump(mode="json")
    values["authority"]["scope"] = contact.authority.scope.model_dump(mode="json")
    return hashlib.sha256(json.dumps({"origin_kind": "bot_contact", "input": values},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


StoreConnection = sqlite3.Connection
StoreRow = sqlite3.Row
_JSON_DOCUMENT = TypeAdapter(dict[str, JsonValue])
_MISSING = object()
_CLIMATE_DIMENSIONS: tuple[str, ...] = (
    "energy",
    "valence",
    "openness",
    "tension",
    "trust",
    "familiarity",
)
_CLIMATE_SENSORS = frozenset(
    {"schedule", "irritation", "circadian", "interaction", "calendar", "message"}
)
_CLIMATE_STATUSES = frozenset({"available", "unavailable", "degraded"})
_CLIMATE_SOURCE_SENSORS = {
    "message": frozenset({"message", "irritation"}),
    "receipt": frozenset({"interaction"}),
    "schedule": frozenset({"schedule"}),
    "calendar": frozenset({"calendar"}),
    "clock": frozenset({"circadian"}),
}
_DELIVERY_ACTIONS = ("message.reply", "message.sticker")
_ARCHIVE_SOURCE_STATUSES = frozenset(
    {"active", "revoked", "deleted", "expired", "quarantined"}
)
_ARCHIVE_RUN_STATUSES = frozenset(
    {"running", "committed", "failed", "cancelled", "abandoned", "needs_rescan"}
)


@dataclass(frozen=True, slots=True)
class SettingsPersonaReceipt:
    """Content-free provenance supplied by the authenticated Settings caller.

    The caller owns authentication; this record neither authenticates an actor
    nor grants activation. Simple personas have no compiled source identity.
    """

    actor: str
    persona_mode: Literal["simple", "source"]
    source_hash: str | None
    compiler_version: str | None
    persona_version: str | None
    saved_revision: int
    effective_revision: int
    decision: Literal["saved", "requested", "applied", "rejected"]
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ClimateSourceProof:
    """Narrow, body-free provenance supplied by an authenticated native owner."""

    key: tuple[str, str, str]
    kind: Literal["message", "receipt", "schedule", "calendar", "clock"]
    source_id: str
    origin_source_id: str
    owner_id: str
    owner_revision: str
    author_id: str
    retained_at: float
    expires_at: float
    event_id: str = ""
    receipt_id: str = ""


@dataclass(frozen=True, slots=True)
class ClimateEvent:
    """A bounded, identity-only Climate event retained for durable de-duplication."""

    sensor: str
    event_id: str
    source_ref: str
    observed_at: float
    source: ClimateSourceProof | None = None


@dataclass(frozen=True, slots=True)
class MemoryExtractionRun:
    """Content-free current execution state for one scoped source revision."""

    run_id: str
    bot_id: str
    group_id: str
    source_id: str
    source_revision: int
    status: str
    stage: str
    error_code: str
    started_at: float
    updated_at: float
    finished_at: float | None


_MEMORY_EXTRACTION_RUN_STATUSES = frozenset(
    {"running", "complete", "cancelled", "unknown", "abandoned"}
)
_MEMORY_EXTRACTION_RUN_STAGES = frozenset(
    {"extracting", "fact", "slang", "style", "episode"}
)


def _memory_extraction_run_result(row: sqlite3.Row) -> MemoryExtractionRun:
    status = row["status"]
    stage = row["stage"]
    if status not in _MEMORY_EXTRACTION_RUN_STATUSES or stage not in _MEMORY_EXTRACTION_RUN_STAGES:
        raise OperationError("invalid_memory_extraction_run")
    finished_at = row["finished_at"]
    return MemoryExtractionRun(
        run_id=str(row["run_id"]),
        bot_id=str(row["bot_id"]),
        group_id=str(row["group_id"]),
        source_id=str(row["source_id"]),
        source_revision=int(row["source_revision"]),
        status=status,
        stage=stage,
        error_code=str(row["error_code"]),
        started_at=float(row["started_at"]),
        updated_at=float(row["updated_at"]),
        finished_at=None if finished_at is None else float(finished_at),
    )


@dataclass(frozen=True, slots=True)
class ClimateStateRecord:
    """Durable Climate state; it intentionally contains no message or prompt text."""

    key: tuple[str, str, str]
    values: tuple[float, ...]
    baselines: tuple[float, ...]
    last_update: float
    revision: int
    generation: str
    source_status: str
    source_refs: tuple[str, ...]
    event_ids: tuple[tuple[str, str], ...]
    source_proofs: tuple[ClimateSourceProof, ...] = ()
    # Set only on the record returned by a commit; reads use the empty tuple.
    evicted_keys: tuple[tuple[str, str, str], ...] = ()


def _upgrade_climate_sources_schema_48(db: sqlite3.Connection) -> None:
    """Isolate every opaque pre-proof epoch without changing any other owner."""
    for row in db.execute("SELECT * FROM climate_states").fetchall():
        key = (str(row["bot_id"]), str(row["group_id"]), str(row["user_id"]))
        count = db.execute("SELECT count(*) FROM climate_events WHERE "
                           "bot_id=? AND group_id=? AND user_id=?", key).fetchone()[0]
        digest = hashlib.sha256(json.dumps(dict(row), sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        db.execute("INSERT INTO audit(kind,identity,revision,code,details) "
                   "VALUES ('climate','schema48',?,'opaque_epoch_isolated',?)",
                   (int(row["revision"]), json.dumps({"scope": key,
                    "generation": row["generation"], "event_count": count,
                    "state_digest": digest}, separators=(",", ":"))))
    db.execute("DELETE FROM climate_events")
    db.execute("DELETE FROM climate_states")
    db.execute("CREATE TABLE climate_sources ("
               "bot_id TEXT NOT NULL,group_id TEXT NOT NULL,user_id TEXT NOT NULL,"
               "generation TEXT NOT NULL,source_id TEXT NOT NULL,kind TEXT NOT NULL "
               "CHECK(kind IN ('message','receipt','schedule','calendar','clock')),"
               "origin_source_id TEXT NOT NULL,owner_id TEXT NOT NULL,owner_revision TEXT NOT NULL,"
               "author_id TEXT NOT NULL,retained_at REAL NOT NULL,expires_at REAL NOT NULL,"
               "event_id TEXT NOT NULL,receipt_id TEXT NOT NULL,"
               "CHECK(expires_at>retained_at AND expires_at<=retained_at+3600),"
               "PRIMARY KEY(bot_id,group_id,user_id,source_id))")
    db.execute("CREATE INDEX climate_sources_origin ON "
               "climate_sources(bot_id,group_id,origin_source_id)")
    db.execute("ALTER TABLE climate_events ADD COLUMN source_id TEXT NOT NULL DEFAULT ''")
    db.execute("PRAGMA user_version=48")


def _upgrade_memory_matters_schema_47(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE memory_matters (
            matter_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL, group_id TEXT NOT NULL, subject_id TEXT NOT NULL,
            source_id TEXT NOT NULL REFERENCES archive_sources(source_id),
            source_revision INTEGER NOT NULL CHECK(source_revision>=1),
            summary TEXT NOT NULL, condition_text TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL CHECK(state IN
                ('candidate','approved','active','completed','cancelled','expired')),
            revision INTEGER NOT NULL CHECK(revision>=1),
            observed_at REAL, due_at REAL, expires_at REAL NOT NULL,
            replaces_matter_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
        )
    """)
    db.execute("CREATE INDEX memory_matters_scope ON "
               "memory_matters(bot_id,group_id,subject_id,state,expires_at,matter_id)")
    db.execute("PRAGMA user_version=47")


def _upgrade_climate_generation_schema_46(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE climate_states ADD COLUMN generation TEXT NOT NULL DEFAULT ''")
    for row in db.execute("SELECT bot_id,group_id,user_id FROM climate_states").fetchall():
        db.execute(
            "UPDATE climate_states SET generation=? WHERE bot_id=? AND group_id=? AND user_id=?",
            (uuid.uuid4().hex, *row),
        )
    db.execute("PRAGMA user_version=46")


def _create_climate_schema(db: sqlite3.Connection) -> None:
    """Create the schema-5 Climate tables inside the caller's transaction."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS climate_states (
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            energy REAL NOT NULL,
            valence REAL NOT NULL,
            openness REAL NOT NULL,
            tension REAL NOT NULL,
            trust REAL NOT NULL,
            familiarity REAL NOT NULL,
            baseline_energy REAL NOT NULL,
            baseline_valence REAL NOT NULL,
            baseline_openness REAL NOT NULL,
            baseline_tension REAL NOT NULL,
            baseline_trust REAL NOT NULL,
            baseline_familiarity REAL NOT NULL,
            last_update REAL NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 0),
            source_status TEXT NOT NULL CHECK(source_status IN ('available','unavailable','degraded')),
            source_refs TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY(bot_id, group_id, user_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS climate_events (
            event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            sensor TEXT NOT NULL,
            event_id TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            observed_at REAL NOT NULL,
            UNIQUE(bot_id, group_id, user_id, sensor, event_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS climate_events_scope "
        "ON climate_events(bot_id,group_id,user_id,event_seq)"
    )


def _create_archive_schema(db: sqlite3.Connection) -> None:
    """Create N6 identity-only archive tables inside the caller's transaction."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS archive_sources (
            source_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            origin_event_id TEXT NOT NULL,
            platform_message_id TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL,
            speaker_kind TEXT NOT NULL,
            speaker_id TEXT NOT NULL DEFAULT '',
            observed_at REAL NOT NULL,
            ingested_at REAL NOT NULL,
            text_expires_at REAL,
            content_digest TEXT,
            body_omitted INTEGER NOT NULL DEFAULT 1 CHECK(body_omitted IN (0,1)),
            source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
            status TEXT NOT NULL CHECK(status IN
                ('active','revoked','deleted','expired','quarantined')),
            visibility_grant_id TEXT NOT NULL DEFAULT '',
            UNIQUE(bot_id, group_id, origin_event_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS archive_sources_scope_observed "
        "ON archive_sources(bot_id,group_id,observed_at,source_id)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS archive_cursors (
            scanner TEXT NOT NULL,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            scanner_version TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            last_committed_marker TEXT NOT NULL DEFAULT '',
            last_observed_at REAL,
            status TEXT NOT NULL CHECK(status IN
                ('running','committed','failed','cancelled','abandoned','needs_rescan')),
            updated_at REAL NOT NULL,
            PRIMARY KEY(scanner,bot_id,group_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS archive_cursors_scope "
        "ON archive_cursors(bot_id,group_id,status)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS archive_scan_runs (
            run_id TEXT PRIMARY KEY,
            scanner TEXT NOT NULL,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            scanner_version TEXT NOT NULL,
            params_hash TEXT NOT NULL,
            run_kind TEXT NOT NULL DEFAULT 'incremental'
                CHECK(run_kind IN ('incremental','rescan')),
            from_marker TEXT NOT NULL,
            to_marker TEXT NOT NULL,
            last_committed_marker TEXT NOT NULL,
            last_page_from_marker TEXT NOT NULL DEFAULT '',
            last_page_to_marker TEXT NOT NULL DEFAULT '',
            last_page_digest TEXT NOT NULL DEFAULT '',
            last_page_committed_count INTEGER NOT NULL DEFAULT 0
                CHECK(last_page_committed_count >= 0),
            scanned_count INTEGER NOT NULL DEFAULT 0 CHECK(scanned_count >= 0),
            emitted_count INTEGER NOT NULL DEFAULT 0 CHECK(emitted_count >= 0),
            committed_count INTEGER NOT NULL DEFAULT 0 CHECK(committed_count >= 0),
            status TEXT NOT NULL CHECK(status IN
                ('running','committed','failed','cancelled','abandoned','needs_rescan')),
            error_code TEXT NOT NULL DEFAULT '',
            started_at REAL NOT NULL,
            finished_at REAL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS archive_scan_runs_scope_time "
        "ON archive_scan_runs(scanner,bot_id,group_id,started_at)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS archive_source_tombstones (
            source_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            speaker_id TEXT NOT NULL DEFAULT '',
            source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
            reason TEXT NOT NULL,
            revoked_at REAL NOT NULL,
            actor TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS archive_source_tombstones_scope "
        "ON archive_source_tombstones(bot_id,group_id,source_id)"
    )


def _create_memory_schema(db: sqlite3.Connection) -> None:
    """Create the small N6 candidate/fact projection schema.

    The memory owner stores only bounded identifiers and source references.  In
    particular, this schema has no column for a message body, summary, URL, or
    image payload.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_candidates (
            candidate_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            value TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('add','reinforce','supersede','skip')),
            target_fact_id TEXT NOT NULL DEFAULT '',
            source_ids TEXT NOT NULL,
            evidence_refs TEXT NOT NULL,
            candidate_revision INTEGER NOT NULL CHECK(candidate_revision >= 1),
            status TEXT NOT NULL CHECK(status IN
                ('pending','conflict_pending','approved','applied','rejected','withdrawn','skipped')),
            conflict_set_id TEXT NOT NULL DEFAULT '',
            skip_reason TEXT NOT NULL DEFAULT '',
            suggestion_reason TEXT NOT NULL DEFAULT '' CHECK(suggestion_reason IN
                ('','stable_preference','time_bounded_plan','communication_boundary',
                 'explicit_correction')),
            applied_fact_id TEXT NOT NULL DEFAULT '',
            applied_event_id TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            observed_at REAL,
            valid_from REAL,
            valid_to REAL
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS memory_candidates_scope_status
        ON memory_candidates(bot_id,group_id,status,created_at,candidate_id)
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_facts (
            fact_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            value TEXT NOT NULL,
            source_ids TEXT NOT NULL,
            evidence_refs TEXT NOT NULL,
            fact_revision INTEGER NOT NULL CHECK(fact_revision >= 1),
            status TEXT NOT NULL CHECK(status IN ('active','superseded','disabled')),
            observed_at REAL,
            valid_from REAL,
            valid_to REAL,
            applied_at REAL,
            suppressed_at REAL,
            suppression_candidate_id TEXT NOT NULL DEFAULT '',
            supersedes_fact_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS memory_facts_active_scope
        ON memory_facts(bot_id,group_id,subject_id,predicate,status)
        """
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS memory_facts_one_active "
        "ON memory_facts(bot_id,group_id,subject_id,predicate) WHERE status='active'"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_facts_supersedes "
        "ON memory_facts(supersedes_fact_id)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_observations (
            observation_id TEXT PRIMARY KEY,
            target_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('add','reinforce','supersede','skip')),
            candidate_id TEXT NOT NULL,
            fact_id TEXT NOT NULL DEFAULT '',
            observed_at REAL NOT NULL,
            actor TEXT NOT NULL,
            UNIQUE(target_id,source_id,action)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_observations_candidate "
        "ON memory_observations(candidate_id,source_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_observations_source "
        "ON memory_observations(source_id,candidate_id,fact_id)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_events (
            event_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            candidate_revision INTEGER NOT NULL CHECK(candidate_revision >= 1),
            action TEXT NOT NULL CHECK(action IN ('add','reinforce','supersede','skip')),
            fact_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN ('applied','cancelled_before_commit')),
            actor TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_events_candidate "
        "ON memory_events(candidate_id,candidate_revision)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_revocations (
            revocation_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
            reason TEXT NOT NULL,
            actor TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','applied')),
            receipt_id TEXT NOT NULL DEFAULT '',
            candidate_count INTEGER NOT NULL DEFAULT 0 CHECK(candidate_count >= 0),
            fact_count INTEGER NOT NULL DEFAULT 0 CHECK(fact_count >= 0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            applied_at REAL,
            UNIQUE(bot_id,group_id,source_id,source_revision)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_revocations_pending "
        "ON memory_revocations(bot_id,group_id,status,created_at,revocation_id)"
    )
    _create_memory_extraction_runs_schema(db)


def _upgrade_memory_schema_16(db: sqlite3.Connection) -> None:
    """Separate legacy application time from optional fact validity in one transaction."""
    candidate_columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info(memory_candidates)")
    }
    for name in ("observed_at", "valid_from", "valid_to"):
        if name not in candidate_columns:
            db.execute(f"ALTER TABLE memory_candidates ADD COLUMN {name} REAL")

    fact_columns = {str(row[1]) for row in db.execute("PRAGMA table_info(memory_facts)")}
    if "suppression_candidate_id" in fact_columns:
        return
    expected = {
        "fact_id", "bot_id", "group_id", "subject_id", "predicate", "value",
        "source_ids", "evidence_refs", "fact_revision", "status", "valid_from",
        "valid_to", "created_at", "updated_at",
    }
    if not expected.issubset(fact_columns):
        raise OperationError("unsupported_schema")
    db.execute("ALTER TABLE memory_facts RENAME TO memory_facts_schema15")
    _create_memory_schema(db)
    db.execute(
        "INSERT INTO memory_facts("
        "fact_id,bot_id,group_id,subject_id,predicate,value,source_ids,evidence_refs,"
        "fact_revision,status,observed_at,valid_from,valid_to,applied_at,"
        "suppressed_at,suppression_candidate_id,created_at,updated_at) "
        "SELECT fact_id,bot_id,group_id,subject_id,predicate,value,source_ids,evidence_refs,"
        "fact_revision,status,NULL,NULL,valid_to,created_at,NULL,'',created_at,updated_at "
        "FROM memory_facts_schema15"
    )
    db.execute("DROP TABLE memory_facts_schema15")
    _create_memory_schema(db)


def _upgrade_memory_schema_17(db: sqlite3.Connection) -> None:
    """Add a bounded, non-evidence extraction reason to memory candidates."""
    candidate_columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info(memory_candidates)")
    }
    if "suggestion_reason" not in candidate_columns:
        db.execute(
            "ALTER TABLE memory_candidates ADD COLUMN suggestion_reason TEXT NOT NULL "
            "DEFAULT '' CHECK(suggestion_reason IN "
            "('','stable_preference','time_bounded_plan','communication_boundary',"
            "'explicit_correction'))"
        )
    db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")


def _upgrade_storylet_schema_18(db: sqlite3.Connection) -> None:
    """Persist Schedule-time Storylet eligibility and its bounded scan cursor."""
    schedule_columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info(schedule_days)")
    }
    if "storylet_fingerprint" not in schedule_columns:
        db.execute("ALTER TABLE schedule_days ADD COLUMN storylet_fingerprint TEXT")
    if "arc_revisions" not in schedule_columns:
        db.execute(
            "ALTER TABLE schedule_days ADD COLUMN arc_revisions TEXT NOT NULL DEFAULT '[]'"
        )
    db.execute(
        "CREATE TABLE IF NOT EXISTS storylet_scan_cursors ("
        "bot_id TEXT NOT NULL,group_id TEXT NOT NULL,last_step INTEGER NOT NULL "
        "CHECK(last_step >= -1),updated_at REAL NOT NULL,"
        "PRIMARY KEY(bot_id,group_id))"
    )
    db.execute(
        "INSERT OR IGNORE INTO storylet_scan_cursors(bot_id,group_id,last_step,updated_at) "
        "SELECT bot_id,group_id,COUNT(*)-1,MAX(updated_at) FROM schedule_days "
        "GROUP BY bot_id,group_id"
    )
    db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")


def _upgrade_style_management_schema_28(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("CREATE TABLE style_scope_versions (bot_id TEXT NOT NULL,group_id TEXT NOT NULL,"
               "revision INTEGER NOT NULL CHECK(revision>=0),PRIMARY KEY(bot_id,group_id))")
    db.execute("CREATE TABLE style_feedback (feedback_id TEXT PRIMARY KEY,bot_id TEXT NOT NULL,"
               "group_id TEXT NOT NULL,object_id TEXT NOT NULL,object_revision INTEGER NOT NULL,"
               "rating TEXT NOT NULL CHECK(rating IN ('positive','negative','neutral')),"
               "actor TEXT NOT NULL,created_at REAL NOT NULL,"
               "FOREIGN KEY(object_id) REFERENCES domain_learning_style_items(object_id))")
    db.execute("CREATE INDEX style_feedback_scope ON style_feedback(bot_id,group_id,created_at)")
    db.execute("CREATE TABLE style_profiles (profile_id TEXT PRIMARY KEY,bot_id TEXT NOT NULL,"
               "group_id TEXT NOT NULL,version INTEGER NOT NULL,revision INTEGER NOT NULL,"
               "status TEXT NOT NULL CHECK(status IN ('draft','enabled','disabled')),"
               "items_json TEXT NOT NULL,actor TEXT NOT NULL,created_at REAL NOT NULL,"
               "UNIQUE(bot_id,group_id,version))")
    db.execute("CREATE UNIQUE INDEX style_profile_enabled ON style_profiles(bot_id,group_id) "
               "WHERE status='enabled'")
    db.execute("PRAGMA user_version=28")


def _upgrade_domain_observations_schema_29(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""
        CREATE TABLE IF NOT EXISTS domain_learning_observations (
            bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
            domain TEXT NOT NULL CHECK(domain IN ('slang','style')),
            pool_key TEXT NOT NULL, source_id TEXT NOT NULL,
            source_revision INTEGER NOT NULL CHECK(source_revision>=1),
            subject_id TEXT NOT NULL, observed_at REAL NOT NULL,
            candidate_id TEXT NOT NULL, candidate_revision INTEGER NOT NULL CHECK(candidate_revision>=1),
            object_id TEXT NOT NULL, object_revision INTEGER NOT NULL CHECK(object_revision>=0),
            PRIMARY KEY(bot_id,group_id,domain,pool_key,source_id),
            FOREIGN KEY(candidate_id) REFERENCES domain_learning_candidates(candidate_id)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS domain_learning_observation_source "
               "ON domain_learning_observations(source_id,source_revision)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS domain_learning_observation_jobs (
            job_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
            pool_key TEXT NOT NULL, candidate_id TEXT NOT NULL,
            candidate_revision INTEGER NOT NULL CHECK(candidate_revision>=1),
            object_id TEXT NOT NULL, object_revision INTEGER NOT NULL CHECK(object_revision>=0),
            chain TEXT NOT NULL CHECK(chain IN ('semantic','backlog')),
            threshold INTEGER NOT NULL CHECK(threshold>=1),
            sources_json TEXT NOT NULL, count INTEGER NOT NULL CHECK(count>=1),
            revision INTEGER NOT NULL CHECK(revision>=1),
            status TEXT NOT NULL CHECK(status IN
                ('queued','approved','rejected','kept','failed','cancelled','stale')),
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(bot_id,group_id,pool_key,candidate_id,candidate_revision,object_revision,chain,threshold),
            FOREIGN KEY(candidate_id) REFERENCES domain_learning_candidates(candidate_id)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS domain_learning_observation_jobs_scope "
               "ON domain_learning_observation_jobs(bot_id,group_id,status,job_id)")
    db.execute("PRAGMA user_version=29")


def _upgrade_knowledge_schema_30(db: sqlite3.Connection) -> None:
    """One owner for explicitly reviewed non-personal Markdown documents."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_sources (
            bot_id TEXT NOT NULL, group_id TEXT NOT NULL, source_id TEXT NOT NULL,
            uploader_id TEXT NOT NULL, source_label TEXT NOT NULL, title TEXT NOT NULL,
            format TEXT NOT NULL CHECK(format='markdown'),
            classification TEXT NOT NULL CHECK(classification='non_personal_document'),
            body TEXT NOT NULL, body_bytes INTEGER NOT NULL CHECK(body_bytes>=0),
            content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
            revision INTEGER NOT NULL CHECK(revision>=1),
            content_revision INTEGER NOT NULL CHECK(content_revision>=1),
            review_status TEXT NOT NULL CHECK(review_status IN ('pending','approved','rejected')),
            reviewed_content_revision INTEGER CHECK(reviewed_content_revision>=1),
            review_actor TEXT,
            status TEXT NOT NULL CHECK(status IN ('inactive','active','removed')),
            apply_actor TEXT,
            upload_policy_revision INTEGER NOT NULL CHECK(upload_policy_revision>=0),
            review_policy_revision INTEGER CHECK(review_policy_revision>=0),
            apply_policy_revision INTEGER CHECK(apply_policy_revision>=0),
            index_version TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY(bot_id,group_id,source_id)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS knowledge_sources_active "
               "ON knowledge_sources(bot_id,group_id,status,review_status)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            bot_id TEXT NOT NULL, group_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_revision INTEGER NOT NULL CHECK(source_revision>=1),
            ordinal INTEGER NOT NULL CHECK(ordinal>=0),
            start_char INTEGER NOT NULL CHECK(start_char>=0),
            end_char INTEGER NOT NULL CHECK(end_char>start_char),
            start_line INTEGER NOT NULL CHECK(start_line>=1),
            end_line INTEGER NOT NULL CHECK(end_line>=start_line),
            title TEXT NOT NULL, body TEXT NOT NULL,
            token_count INTEGER NOT NULL CHECK(token_count>=0),
            PRIMARY KEY(bot_id,group_id,chunk_id),
            UNIQUE(bot_id,group_id,source_id,ordinal),
            FOREIGN KEY(bot_id,group_id,source_id)
                REFERENCES knowledge_sources(bot_id,group_id,source_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_chunk_terms (
            bot_id TEXT NOT NULL, group_id TEXT NOT NULL, term TEXT NOT NULL,
            chunk_id TEXT NOT NULL, frequency INTEGER NOT NULL CHECK(frequency>0),
            PRIMARY KEY(bot_id,group_id,term,chunk_id),
            FOREIGN KEY(bot_id,group_id,chunk_id)
                REFERENCES knowledge_chunks(bot_id,group_id,chunk_id) ON DELETE CASCADE
        )
    """)
    db.execute("PRAGMA user_version=30")


def _upgrade_character_identity_schema_31(db: sqlite3.Connection) -> None:
    """Exact human image teaching; no image bytes or personal memory projection."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""
        CREATE TABLE character_identities (
            identity_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
            teacher_id TEXT NOT NULL, image_sha256 TEXT NOT NULL CHECK(length(image_sha256)=64),
            label TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1),
            status TEXT NOT NULL CHECK(status IN ('active','revoked')),
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(bot_id,group_id,teacher_id,image_sha256)
        )
    """)
    db.execute("""
        CREATE TABLE character_identity_sources (
            identity_id TEXT NOT NULL, identity_revision INTEGER NOT NULL,
            source_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
            origin_event_id TEXT NOT NULL, platform_message_id TEXT NOT NULL,
            turn_id TEXT NOT NULL, segment_index INTEGER NOT NULL, source_kind TEXT NOT NULL
                CHECK(source_kind='direct'),
            PRIMARY KEY(identity_id,identity_revision,source_id),
            FOREIGN KEY(identity_id) REFERENCES character_identities(identity_id),
            FOREIGN KEY(source_id) REFERENCES archive_sources(source_id)
        )
    """)
    db.execute("""
        CREATE TABLE character_identity_receipts (
            receipt_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
            identity_id TEXT NOT NULL, identity_revision INTEGER NOT NULL,
            request_digest TEXT NOT NULL, action TEXT NOT NULL
                CHECK(action IN ('teach','reinforce','rename','revoke')),
            status TEXT NOT NULL CHECK(status IN ('active','revoked')),
            audit_id INTEGER NOT NULL, policy_revision INTEGER NOT NULL, created_at REAL NOT NULL,
            FOREIGN KEY(identity_id) REFERENCES character_identities(identity_id),
            FOREIGN KEY(audit_id) REFERENCES audit(id)
        )
    """)
    db.execute("PRAGMA user_version=31")


def _upgrade_graph_schema_32(db: sqlite3.Connection) -> None:
    """Manual document-backed concept projections; no second fact/source owner."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""
        CREATE TABLE graph_entities (
            bot_id TEXT NOT NULL, group_id TEXT NOT NULL, entity_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind='concept'),
            PRIMARY KEY(bot_id,group_id,entity_id)
        )
    """)
    for table, fields in (
        ("graph_relations", "subject_id TEXT NOT NULL, predicate TEXT NOT NULL, target_id TEXT NOT NULL"),
        ("graph_aliases", "entity_id TEXT NOT NULL, alias_surface TEXT NOT NULL, alias_norm TEXT NOT NULL"),
    ):
        db.execute(f"""
            CREATE TABLE {table} (
                bot_id TEXT NOT NULL, group_id TEXT NOT NULL, object_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision>=1), {fields},
                pointer_json TEXT NOT NULL, payload_digest TEXT NOT NULL,
                review_status TEXT NOT NULL CHECK(review_status IN ('pending','approved','rejected')),
                status TEXT NOT NULL CHECK(status IN ('inactive','active','ambiguous','revoked')),
                review_actor TEXT, apply_actor TEXT, policy_revision INTEGER NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY(bot_id,group_id,object_id)
            )
        """)
    db.execute("CREATE INDEX graph_relations_neighbors "
               "ON graph_relations(bot_id,group_id,status,subject_id,object_id)")
    db.execute("CREATE INDEX graph_aliases_resolve "
               "ON graph_aliases(bot_id,group_id,alias_norm,status,object_id)")
    db.execute("PRAGMA user_version=32")



def _upgrade_memory_cards_schema_33(db: sqlite3.Connection) -> None:
    """Classification references only; cleared rows retain the CAS revision."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""
        CREATE TABLE memory_card_classifications (
            bot_id TEXT NOT NULL, group_id TEXT NOT NULL, fact_id TEXT NOT NULL,
            category TEXT CHECK(category IN
                ('preference','boundary','relationship','event','promise','fact','status')),
            classification_revision INTEGER NOT NULL CHECK(classification_revision>=1),
            actor TEXT NOT NULL, policy_revision INTEGER NOT NULL, updated_at REAL NOT NULL,
            PRIMARY KEY(bot_id,group_id,fact_id),
            FOREIGN KEY(fact_id) REFERENCES memory_facts(fact_id)
        )
    """)
    db.execute("PRAGMA user_version=33")


def _upgrade_affection_schema_34(db: sqlite3.Connection) -> None:
    """Body-free, source/receipt-derived same-group relationship contributions."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE affection_relations (
        bot_id TEXT NOT NULL, group_id TEXT NOT NULL, subject_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>=1),
        PRIMARY KEY(bot_id,group_id,subject_id))""")
    db.execute("""CREATE TABLE affection_contributions (
        bot_id TEXT NOT NULL, group_id TEXT NOT NULL, subject_id TEXT NOT NULL,
        event_id TEXT NOT NULL, source_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
        source_digest TEXT NOT NULL,
        action_id TEXT NOT NULL, receipt_id TEXT NOT NULL, day TEXT NOT NULL,
        revision INTEGER NOT NULL, actor TEXT NOT NULL, policy_revision INTEGER NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY(bot_id,group_id,subject_id,event_id),
        UNIQUE(bot_id,group_id,action_id),
        FOREIGN KEY(source_id) REFERENCES archive_sources(source_id),
        FOREIGN KEY(action_id) REFERENCES actions(id))""")
    db.execute("PRAGMA user_version=34")


def _upgrade_affection_adjustments_schema_42(db: sqlite3.Connection) -> None:
    """Administrator evidence is distinct from real reply contributions."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE affection_adjustments (
        bot_id TEXT NOT NULL,group_id TEXT NOT NULL,subject_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>=1),offset REAL NOT NULL,
        requested_score REAL NOT NULL CHECK(requested_score>=0 AND requested_score<=100),
        actor TEXT NOT NULL,operation_id TEXT NOT NULL UNIQUE,digest TEXT NOT NULL,
        policy_revision INTEGER NOT NULL,updated_at REAL NOT NULL,
        PRIMARY KEY(bot_id,group_id,subject_id))""")
    db.execute("PRAGMA user_version=42")


def _upgrade_food_private_schema_43(db: sqlite3.Connection) -> None:
    """Private food state is independent of every group preference row."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE food_private_preferences (
        bot_id TEXT NOT NULL,private_user_id TEXT NOT NULL,user_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0),likes TEXT NOT NULL,dislikes TEXT NOT NULL,
        location TEXT NOT NULL,CHECK(private_user_id=user_id),
        PRIMARY KEY(bot_id,private_user_id,user_id))""")
    db.execute("""CREATE TABLE food_private_served (
        bot_id TEXT NOT NULL,private_user_id TEXT NOT NULL,user_id TEXT NOT NULL,event_id TEXT NOT NULL,
        name TEXT NOT NULL,action_id TEXT NOT NULL,receipt_id TEXT NOT NULL,served_at REAL NOT NULL,
        CHECK(private_user_id=user_id),PRIMARY KEY(bot_id,private_user_id,user_id,event_id))""")
    db.execute("PRAGMA user_version=43")


def _upgrade_food_schema_35(db: sqlite3.Connection) -> None:
    """Food user consent state; transient feedback stays outside SQLite."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE food_preferences (
        bot_id TEXT NOT NULL,group_id TEXT NOT NULL,user_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>0),likes TEXT NOT NULL,dislikes TEXT NOT NULL,
        location TEXT NOT NULL,PRIMARY KEY(bot_id,group_id,user_id))""")
    db.execute("""CREATE TABLE food_served (
        bot_id TEXT NOT NULL,group_id TEXT NOT NULL,user_id TEXT NOT NULL,event_id TEXT NOT NULL,
        name TEXT NOT NULL,action_id TEXT NOT NULL,receipt_id TEXT NOT NULL,served_at REAL NOT NULL,
        PRIMARY KEY(bot_id,group_id,user_id,event_id))""")
    db.execute("""CREATE TABLE food_tutorial_claims (
        claim_key TEXT PRIMARY KEY,user_id TEXT NOT NULL,claimed_at REAL NOT NULL)""")
    db.execute("PRAGMA user_version=35")


def _upgrade_action_scope_schema_36(db: sqlite3.Connection) -> None:
    """Preserve group action identities while making private targets explicit."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE actions_v36 (
        id TEXT PRIMARY KEY, request_id TEXT NOT NULL, digest TEXT NOT NULL,
        state TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL,
        code TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL, bot_id TEXT NOT NULL,
        group_id TEXT, scope_kind TEXT NOT NULL DEFAULT 'group', private_user_id TEXT,
        receipt TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL DEFAULT (unixepoch()),
        CHECK((scope_kind='group' AND group_id IS NOT NULL AND private_user_id IS NULL)
           OR (scope_kind='private' AND group_id IS NULL AND private_user_id IS NOT NULL)))""")
    db.execute("""INSERT INTO actions_v36
        (rowid,id,request_id,digest,state,revision,action,code,subject,bot_id,group_id,receipt,created_at)
        SELECT rowid,id,request_id,digest,state,revision,action,code,subject,bot_id,group_id,
               receipt,created_at
        FROM actions""")
    db.execute("DROP TABLE actions")
    db.execute("ALTER TABLE actions_v36 RENAME TO actions")
    db.execute("PRAGMA user_version=36")


def _upgrade_visibility_policy_schema_37(db: sqlite3.Connection) -> None:
    """Add empty sharing permission metadata without migrating legacy visibility."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT revision,CAST(grants AS BLOB) FROM policy WHERE id=1").fetchone()
    if row is None or type(row[0]) is not int or row[0] < 0 or not isinstance(row[1], bytes):
        raise OperationError("invalid_policy")
    revision, ordinary = row[0], row[1]
    digest = hashlib.sha256(ordinary).hexdigest()
    if revision:
        prior = db.execute(
            "SELECT revision,details FROM audit WHERE kind='policy' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        try:
            valid = (prior is not None and prior[0] == revision
                     and json.loads(prior[1])["new"] == digest)
        except (ValueError, TypeError, KeyError) as exc:
            raise OperationError("invalid_policy") from exc
        if not valid:
            raise OperationError("invalid_policy")
    else:
        try:
            if json.loads(ordinary) != []:
                raise OperationError("invalid_policy")
        except (ValueError, TypeError) as exc:
            raise OperationError("invalid_policy") from exc
    db.execute("ALTER TABLE policy ADD COLUMN visibility_grants TEXT NOT NULL DEFAULT '[]'")
    # Keep every historical audit row and ordinary grant byte unchanged. The
    # append establishes the additional empty document at the same policy epoch.
    db.execute(
        "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('policy',?,?,?,?)",
        ("schema37", revision, "visibility_schema_initialized",
         json.dumps({"old": digest, "new": digest,
                     "visibility_new": hashlib.sha256(b"[]").hexdigest()})),
    )
    db.execute("PRAGMA user_version=37")


def _upgrade_contact_schema_49(db: sqlite3.Connection) -> None:
    """Add closed contact consent and honest request origin without importing state."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    row = db.execute(
        "SELECT revision,CAST(grants AS BLOB),CAST(visibility_grants AS BLOB) "
        "FROM policy WHERE id=1",
    ).fetchone()
    if (row is None or type(row[0]) is not int or row[0] < 0
            or not isinstance(row[1], bytes) or not isinstance(row[2], bytes)):
        raise OperationError("invalid_policy")
    revision, ordinary, visibility = row[0], row[1], row[2]
    ordinary_hash = hashlib.sha256(ordinary).hexdigest()
    visibility_hash = hashlib.sha256(visibility).hexdigest()
    try:
        if revision:
            prior = db.execute(
                "SELECT revision,details FROM audit WHERE kind='policy' ORDER BY id DESC LIMIT 1",
            ).fetchone()
            details = json.loads(prior[1]) if prior is not None else None
            if (prior is None or prior[0] != revision or not isinstance(details, dict)
                    or details["new"] != ordinary_hash or details["visibility_new"] != visibility_hash):
                raise OperationError("invalid_policy")
        elif json.loads(ordinary) != [] or json.loads(visibility) != []:
            raise OperationError("invalid_policy")
    except (ValueError, TypeError, KeyError) as exc:
        raise OperationError("invalid_policy") from exc
    db.execute("ALTER TABLE policy ADD COLUMN contact_consents TEXT NOT NULL DEFAULT '[]'")
    db.execute(
        "ALTER TABLE requests ADD COLUMN origin_kind TEXT NOT NULL DEFAULT 'inbound_event' "
        "CHECK(origin_kind IN ('inbound_event','bot_contact'))",
    )
    db.execute("""
        CREATE TABLE contact_requests (
            request_id TEXT PRIMARY KEY REFERENCES requests(id),
            bot_id TEXT NOT NULL, group_id TEXT NOT NULL, target_user_id TEXT NOT NULL,
            purpose TEXT NOT NULL CHECK(purpose IN ('autonomous_chat','role_life_broadcast')),
            cause_kind TEXT NOT NULL CHECK(cause_kind IN ('conversation_context','role_life')),
            cause_id TEXT NOT NULL, cause_version TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            authority_revision INTEGER NOT NULL CHECK(authority_revision >= 0),
            user_generation TEXT NOT NULL, group_generation TEXT NOT NULL,
            created_at REAL NOT NULL, expires_at REAL NOT NULL CHECK(expires_at > created_at),
            revision INTEGER NOT NULL CHECK(revision >= 1),
            active INTEGER NOT NULL CHECK(active IN (0,1)),
            decision TEXT NOT NULL DEFAULT '' CHECK(decision IN ('','now','defer','abandon')),
            reason_code TEXT NOT NULL DEFAULT '', due_at REAL,
            decision_started_at REAL, send_started_at REAL,
            UNIQUE(bot_id,group_id,cause_kind,cause_id,cause_version)
        )
    """)
    db.execute(
        "CREATE UNIQUE INDEX contact_current_group ON contact_requests(bot_id,group_id) WHERE active=1",
    )
    db.execute(
        "CREATE INDEX contact_user_usage ON contact_requests(bot_id,target_user_id,created_at)",
    )
    db.execute(
        "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('policy','schema49',?,?,?)",
        (revision, "contact_schema_initialized", json.dumps({
            "old": ordinary_hash, "new": ordinary_hash,
            "visibility_old": visibility_hash, "visibility_new": visibility_hash,
            "contact_old": hashlib.sha256(b"[]").hexdigest(),
            "contact_new": hashlib.sha256(b"[]").hexdigest(),
        }, separators=(",", ":"))),
    )
    db.execute("PRAGMA user_version=49")


def _upgrade_sticker_schema_38(db: sqlite3.Connection) -> None:
    """Admin-controlled immutable assets and same-transaction operation receipts."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE sticker_catalog_head (
        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL CHECK(revision>=0))""")
    db.execute("INSERT INTO sticker_catalog_head VALUES (1,0)")
    db.execute("""CREATE TABLE sticker_assets (
        sticker_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
        content_hash TEXT NOT NULL, mime_type TEXT NOT NULL, byte_size INTEGER NOT NULL,
        width INTEGER NOT NULL, height INTEGER NOT NULL, image_data BLOB NOT NULL,
        description TEXT NOT NULL, usage_hint TEXT NOT NULL, ocr_text TEXT NOT NULL,
        intent_tags TEXT NOT NULL, affect_tags TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','approved','revoked')),
        revision INTEGER NOT NULL CHECK(revision>0))""")
    db.execute("""CREATE TABLE sticker_operations (
        operation_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
        actor TEXT NOT NULL, operation TEXT NOT NULL, digest TEXT NOT NULL,
        catalog_revision INTEGER NOT NULL, sticker_id TEXT NOT NULL,
        entry_revision INTEGER NOT NULL, status TEXT NOT NULL,
        policy_revision INTEGER NOT NULL, created_at REAL NOT NULL,
        FOREIGN KEY(sticker_id) REFERENCES sticker_assets(sticker_id))""")
    db.execute("PRAGMA user_version=38")


def _upgrade_episode_schema_41(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(domain_learning_episodes)")}
    if "decay_at" not in columns:
        db.execute("ALTER TABLE domain_learning_episodes ADD COLUMN decay_at TEXT NOT NULL DEFAULT ''")
    if "last_used_at" not in columns:
        db.execute("ALTER TABLE domain_learning_episodes ADD COLUMN last_used_at REAL")
    db.execute("PRAGMA user_version=41")


def _upgrade_self_fact_graph_schema_40(db: sqlite3.Connection) -> None:
    """Expand only entity provenance kinds; existing document rows remain identical."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""
        CREATE TABLE graph_entities_v40 (
            bot_id TEXT NOT NULL, group_id TEXT NOT NULL, entity_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('concept','self_subject')),
            PRIMARY KEY(bot_id,group_id,entity_id)
        )
    """)
    db.execute("INSERT INTO graph_entities_v40 SELECT * FROM graph_entities")
    db.execute("DROP TABLE graph_entities")
    db.execute("ALTER TABLE graph_entities_v40 RENAME TO graph_entities")
    db.execute("PRAGMA user_version=40")


def _upgrade_qq_delivery_schema_45(db: sqlite3.Connection) -> None:
    """Add identity-only governance without rewriting existing action evidence."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(actions)")}
    for name, declaration in _QQ_ACTION_COLUMNS.items():
        if name not in columns:
            db.execute(f"ALTER TABLE actions ADD COLUMN {name} {declaration}")
    db.execute("""CREATE TABLE IF NOT EXISTS qq_delivery_state (
        account_id TEXT NOT NULL, scope_key TEXT NOT NULL,
        held INTEGER NOT NULL CHECK(held IN (0,1)),
        revision INTEGER NOT NULL CHECK(revision>=0), reason TEXT NOT NULL,
        occurred_at REAL NOT NULL, last_settled_at REAL, action_id TEXT NOT NULL DEFAULT '',
        audit_id INTEGER, PRIMARY KEY(account_id,scope_key))""")
    db.execute("""CREATE TABLE IF NOT EXISTS qq_delivery_metadata (
        id INTEGER PRIMARY KEY CHECK(id=1), database_id TEXT NOT NULL UNIQUE)""")
    db.execute("INSERT OR IGNORE INTO qq_delivery_metadata VALUES (1,?)", (str(uuid.uuid4()),))
    db.execute("CREATE INDEX IF NOT EXISTS actions_qq_account_budget "
               "ON actions(qq_account_id,qq_budget_anchor)")
    db.execute("CREATE INDEX IF NOT EXISTS actions_qq_target_budget "
               "ON actions(qq_account_id,scope_kind,group_id,private_user_id,qq_budget_anchor)")
    db.execute("PRAGMA user_version=45")


def _qq_scope_encoded(account_id: str, scope_key: QQScopeKey | None) -> str:
    if scope_key is None:
        return ""
    if scope_key[0] != account_id:
        raise OperationError("qq_scope_mismatch")
    return json.dumps(scope_key, separators=(",", ":"), ensure_ascii=False)


def _qq_state_row(row: sqlite3.Row) -> QQStateRow:
    key = str(row["scope_key"])
    scope_key = None if not key else cast(QQScopeKey, tuple(json.loads(key)))
    return QQStateRow(
        account_id=str(row["account_id"]), scope_key=scope_key, held=bool(row["held"]),
        revision=int(row["revision"]), reason=str(row["reason"]), occurred_at=float(row["occurred_at"]),
        last_settled_at=None if row["last_settled_at"] is None else float(row["last_settled_at"]),
        action_id=str(row["action_id"]), audit_id=None if row["audit_id"] is None else int(row["audit_id"]),
    )


def _qq_action_rows(db: sqlite3.Connection, account_id: str, now: float) -> list[sqlite3.Row]:
    """Legacy evidence remains attached to its original verified Bot identity."""
    marks = ",".join("?" for _ in _QQ_LEGACY_WRITES)
    return db.execute(
        "SELECT actions.*,review.kind AS review_kind,review.identity AS review_account,"
        "review.details AS review_details,COALESCE(qq_budget_anchor,actions.created_at) AS budget_anchor,"
        "CASE WHEN qq_account_id IS NULL THEN 1 ELSE qq_cost END AS cost "
        "FROM actions LEFT JOIN audit review ON review.id=actions.qq_review_ref WHERE "
        "(qq_account_id=? OR "
        f"(qq_account_id IS NULL AND bot_id=? AND action IN ({marks}))) AND "
        "(COALESCE(qq_budget_anchor,actions.created_at)>? OR state IN ('unknown','dispatching')) "
        "ORDER BY budget_anchor,actions.id", (account_id, account_id, *_QQ_LEGACY_WRITES, now - 86400),
    ).fetchall()


def _qq_unknown_reviewed(row: sqlite3.Row, account_id: str) -> bool:
    if (row["qq_review_ref"] is None or row["review_kind"] != "qq_resume"
            or row["review_account"] != account_id):
        return False
    details = _JSON_DOCUMENT.validate_json(str(row["review_details"]))
    refs = details.get("reviewed_unknown_action_ids")
    return isinstance(refs, list) and str(row["id"]) in refs


def _qq_matches_scope(row: sqlite3.Row, scope_key: QQScopeKey | None) -> bool:
    if scope_key is None:
        return True
    target = row["group_id"] if scope_key[1] == "group" else row["private_user_id"]
    return row["scope_kind"] == scope_key[1] and target == scope_key[2]


def _qq_ensure_state(
    db: sqlite3.Connection, account_id: str, scope_key: QQScopeKey | None, now: float,
) -> QQStateRow:
    encoded = _qq_scope_encoded(account_id, scope_key)
    if scope_key is not None and db.execute(
        "SELECT 1 FROM qq_delivery_state WHERE account_id=? AND scope_key=''", (account_id,),
    ).fetchone() is None:
        raise OperationError("qq_account_uninitialized")
    db.execute(
        "INSERT OR IGNORE INTO qq_delivery_state(account_id,scope_key,held,revision,reason,occurred_at) "
        "VALUES (?,?,?,0,?,?)",
        (account_id, encoded, int(scope_key is None), "initial_held" if scope_key is None else "", now),
    )
    row = db.execute(
        "SELECT * FROM qq_delivery_state WHERE account_id=? AND scope_key=?", (account_id, encoded),
    ).fetchone()
    assert row is not None
    return _qq_state_row(row)


def _qq_hold_transaction(
    db: sqlite3.Connection, state: QQStateRow, *, reason: str, actor: str, now: float,
    action_id: str = "", evidence: QQTransportEvidence | None = None,
) -> QQStateRow:
    details: dict[str, object] = {"actor": actor, "scope_key": state.scope_key, "action_id": action_id}
    if evidence is not None:
        details["evidence"] = evidence.safe_dump()
    cursor = db.execute(
        "INSERT INTO audit(kind,identity,revision,code,details,created_at) VALUES ('qq_hold',?,?,?,?,?)",
        (state.account_id, state.revision + 1, reason, json.dumps(details, separators=(",", ":")), now),
    )
    db.execute(
        "UPDATE qq_delivery_state SET held=1,revision=revision+1,reason=?,occurred_at=?,action_id=?,"
        "audit_id=? WHERE account_id=? AND scope_key=? AND revision=?",
        (reason, now, action_id, cursor.lastrowid, state.account_id,
         _qq_scope_encoded(state.account_id, state.scope_key), state.revision),
    )
    return _qq_ensure_state(db, state.account_id, state.scope_key, now)


def _qq_recover_account(db: sqlite3.Connection, account_id: str, now: float) -> QQStateRow:
    state = _qq_ensure_state(db, account_id, None, now)
    rows = _qq_action_rows(db, account_id, now)
    unknown = [row for row in rows if row["state"] == "unknown" and not _qq_unknown_reviewed(row, account_id)]
    future = any(float(row["budget_anchor"]) > now for row in rows)
    future = future or (state.last_settled_at is not None and state.last_settled_at > now)
    interrupted_batch = state.reason.startswith("test_batch_active:")
    if (unknown or future or interrupted_batch) and not state.held:
        return _qq_hold_transaction(
            db, state, reason="clock_inconsistent" if future else "unknown" if unknown
            else "qq_test_batch_interrupted", actor="startup",
            now=now, action_id=str(unknown[-1]["id"]) if unknown else "",
        )
    return state


def _upgrade_research_journal_schema_44(db: sqlite3.Connection) -> None:
    """Preserve fiction/reviews while adding explicit public sources and research evidence."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE journal_drafts_v44 (
        draft_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
        root_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1),
        supersedes_draft_id TEXT UNIQUE, source_event_id TEXT NOT NULL,
        source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
        body TEXT NOT NULL CHECK((content_kind='fiction' AND length(body)>6
            AND substr(body,1,6)='虚构故事里，') OR
            (content_kind='factual' AND length(body)>0 AND length(body)<=280)),
        content_kind TEXT NOT NULL DEFAULT 'fiction' CHECK(content_kind IN ('fiction','factual')),
        body_hash TEXT NOT NULL CHECK(length(body_hash)=64),
        content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
        state TEXT NOT NULL CHECK(state IN ('pending_review','approved','rejected')),
        created_at REAL NOT NULL,
        UNIQUE(root_id,revision), UNIQUE(bot_id,group_id,source_event_id,revision),
        FOREIGN KEY(supersedes_draft_id) REFERENCES journal_drafts(draft_id))""")
    db.execute("""CREATE TABLE journal_reviews_v44 (
        draft_id TEXT PRIMARY KEY, actor TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
        body_hash TEXT NOT NULL CHECK(length(body_hash)=64),
        source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
        content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
        approval_scope TEXT NOT NULL CHECK(approval_scope IN ('dry_run','live')), created_at REAL NOT NULL,
        FOREIGN KEY(draft_id) REFERENCES journal_drafts(draft_id))""")
    columns = ("draft_id,bot_id,group_id,root_id,revision,supersedes_draft_id,source_event_id,"
               "source_hash,body,body_hash,content_hash,state,created_at")
    db.execute(f"INSERT INTO journal_drafts_v44({columns}) SELECT {columns} FROM journal_drafts")
    db.execute("INSERT INTO journal_reviews_v44 SELECT * FROM journal_reviews")
    db.execute("DROP TABLE journal_reviews")
    db.execute("DROP TABLE journal_drafts")
    db.execute("ALTER TABLE journal_drafts_v44 RENAME TO journal_drafts")
    db.execute("ALTER TABLE journal_reviews_v44 RENAME TO journal_reviews")
    db.execute("""CREATE TABLE journal_public_consents (
 source_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
 source_revision INTEGER NOT NULL, author TEXT NOT NULL, event_id TEXT NOT NULL,
 message_id TEXT NOT NULL, text_hash TEXT NOT NULL CHECK(length(text_hash)=64),
 template_id TEXT NOT NULL, labels_json TEXT NOT NULL, label_index INTEGER NOT NULL,
 expires_at REAL NOT NULL, created_at REAL NOT NULL
)""")
    db.execute("""CREATE TABLE journal_public_sources (
 source_event_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
 source_hash TEXT NOT NULL CHECK(length(source_hash)=64), template_id TEXT NOT NULL,
 labels_json TEXT NOT NULL, consent_ids_json TEXT NOT NULL, expires_at REAL NOT NULL,
 created_at REAL NOT NULL
)""")
    db.execute("""CREATE TABLE journal_compositions (
 operation_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
 source_event_id TEXT NOT NULL, source_hash TEXT NOT NULL, event_date TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('composing','complete','failed')),
 created_at REAL NOT NULL, UNIQUE(bot_id,group_id,source_event_id)
)""")
    db.execute("""CREATE TABLE journal_deliveries (
 delivery_id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, root_id TEXT NOT NULL,
 bot_id TEXT NOT NULL, group_id TEXT NOT NULL, actor TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('dry_run','live')), payload_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('dispatching','unknown','published','failed')),
 action_key TEXT NOT NULL UNIQUE, receipt TEXT NOT NULL DEFAULT '', code TEXT NOT NULL DEFAULT '',
 created_at REAL NOT NULL, finished_at REAL,
 UNIQUE(root_id,mode), FOREIGN KEY(draft_id) REFERENCES journal_drafts(draft_id)
)""")
    db.execute("""CREATE TABLE research_message_events (
    event_uid TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    bot_key TEXT NOT NULL,
    group_key TEXT NOT NULL,
    actor_key TEXT NOT NULL,
    permission_subject_key TEXT NOT NULL,
    event_time REAL NOT NULL,
    ingested_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    source_expires_at REAL NOT NULL,
    source_revision INTEGER NOT NULL CHECK(source_revision=1),
    policy_revision INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    spool_id TEXT NOT NULL UNIQUE,
    direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
    actor_type TEXT NOT NULL CHECK(actor_type IN ('human','ai')),
    source TEXT NOT NULL CHECK(source IN ('live','history','offline')),
    origin TEXT NOT NULL CHECK(origin IN ('human','main_llm','scheduler')),
    event_time_origin TEXT NOT NULL CHECK(event_time_origin IN ('platform','sender_success')),
    message_key TEXT NOT NULL,
    reply_key TEXT NOT NULL,
    mention_keys TEXT NOT NULL,
    content_type TEXT NOT NULL,
    action_key TEXT NOT NULL,
    action_digest TEXT NOT NULL,
    CHECK((direction='inbound' AND actor_type='human' AND action_key='')
       OR (direction='outbound' AND actor_type='ai' AND action_key<>'')),
    CHECK(source_expires_at <= event_time+86400)
)""")
    db.execute("""CREATE INDEX research_message_events_scope_time
    ON research_message_events(bot_key, group_key, event_time, event_uid)""")
    db.execute("PRAGMA user_version=44")


def _upgrade_journal_schema_39(db: sqlite3.Connection) -> None:
    """Local fiction draft revisions and exact-content dry-run reviews."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE journal_drafts (
        draft_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
        root_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1),
        supersedes_draft_id TEXT UNIQUE, source_event_id TEXT NOT NULL,
        source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
        body TEXT NOT NULL CHECK(length(body)>6 AND substr(body,1,6)='虚构故事里，'),
        body_hash TEXT NOT NULL CHECK(length(body_hash)=64),
        content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
        state TEXT NOT NULL CHECK(state IN ('pending_review','approved','rejected')),
        created_at REAL NOT NULL,
        UNIQUE(root_id,revision), UNIQUE(bot_id,group_id,source_event_id,revision),
        FOREIGN KEY(supersedes_draft_id) REFERENCES journal_drafts(draft_id))""")
    db.execute("""CREATE TABLE journal_reviews (
        draft_id TEXT PRIMARY KEY, actor TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
        body_hash TEXT NOT NULL CHECK(length(body_hash)=64),
        source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
        content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
        approval_scope TEXT NOT NULL CHECK(approval_scope='dry_run'), created_at REAL NOT NULL,
        FOREIGN KEY(draft_id) REFERENCES journal_drafts(draft_id))""")
    db.execute("""CREATE TABLE journal_operations (
        operation_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
        actor TEXT NOT NULL, operation TEXT NOT NULL, digest TEXT NOT NULL,
        result_draft_id TEXT NOT NULL, policy_revision INTEGER NOT NULL, created_at REAL NOT NULL,
        FOREIGN KEY(result_draft_id) REFERENCES journal_drafts(draft_id))""")
    db.execute("PRAGMA user_version=39")


def _create_domain_learning_schema(db: sqlite3.Connection) -> None:
    """Create the source-bound, per-domain N6 learning ledger."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_learning_results (
            result_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            domain TEXT NOT NULL CHECK(domain IN ('slang','style','episode')),
            extractor_version TEXT NOT NULL,
            source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
            result_status TEXT NOT NULL CHECK(result_status IN ('candidates','no_evidence')),
            result_digest TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(bot_id,group_id,source_id,domain,extractor_version)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_learning_candidates (
            candidate_id TEXT PRIMARY KEY,
            result_id TEXT NOT NULL,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            domain TEXT NOT NULL CHECK(domain IN ('slang','style','episode')),
            extractor_version TEXT NOT NULL,
            normalization_key TEXT NOT NULL,
            payload TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            candidate_revision INTEGER NOT NULL CHECK(candidate_revision >= 1),
            review_status TEXT NOT NULL CHECK(review_status IN
                ('pending','approved','rejected','withdrawn')),
            application_status TEXT NOT NULL CHECK(application_status IN
                ('not_applied','applied','disabled')),
            episode_state TEXT CHECK(episode_state IS NULL OR episode_state IN
                ('dry_run','candidate','approved','enabled_for_prompt','disabled')),
            applied_object_id TEXT NOT NULL DEFAULT '',
            applied_event_id TEXT NOT NULL DEFAULT '',
            last_event_id TEXT NOT NULL DEFAULT '',
            last_event_action TEXT NOT NULL DEFAULT '',
            last_event_digest TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(result_id,normalization_key),
            UNIQUE(bot_id,group_id,source_id,domain,extractor_version,normalization_key),
            FOREIGN KEY(result_id) REFERENCES domain_learning_results(result_id),
            CHECK((domain='episode' AND episode_state IS NOT NULL) OR
                  (domain<>'episode' AND episode_state IS NULL))
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS domain_learning_candidates_review "
        "ON domain_learning_candidates(bot_id,group_id,domain,review_status,created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS domain_learning_candidates_source "
        "ON domain_learning_candidates(bot_id,group_id,source_id,domain)"
    )
    _create_domain_learning_failures_table(db)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_learning_slang_terms (
            object_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL UNIQUE,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            term TEXT NOT NULL,
            term_key TEXT NOT NULL,
            meaning TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            object_revision INTEGER NOT NULL CHECK(object_revision >= 1),
            status TEXT NOT NULL CHECK(status IN ('active','disabled')),
            applied_event_id TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES domain_learning_candidates(candidate_id)
        )
        """
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS domain_learning_slang_keys ("
        "bot_id TEXT NOT NULL,group_id TEXT NOT NULL,normalized_key TEXT NOT NULL,"
        "object_id TEXT NOT NULL,key_kind TEXT NOT NULL CHECK(key_kind IN ('term','alias')),"
        "PRIMARY KEY(bot_id,group_id,normalized_key),"
        "FOREIGN KEY(object_id) REFERENCES domain_learning_slang_terms(object_id))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS domain_learning_slang_stoplist ("
        "bot_id TEXT NOT NULL,group_id TEXT NOT NULL,normalized_key TEXT NOT NULL,"
        "actor TEXT NOT NULL,reason TEXT NOT NULL,created_at REAL NOT NULL,"
        "PRIMARY KEY(bot_id,group_id,normalized_key))"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS domain_learning_slang_scope "
        "ON domain_learning_slang_terms(bot_id,group_id,status,updated_at)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_learning_style_items (
            object_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL UNIQUE,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            situation TEXT NOT NULL,
            situation_key TEXT NOT NULL,
            style TEXT NOT NULL,
            output_policy TEXT NOT NULL CHECK(output_policy IN
                ('allow_use','transform','observe_only')),
            risk_tags_json TEXT NOT NULL,
            object_revision INTEGER NOT NULL CHECK(object_revision >= 1),
            status TEXT NOT NULL CHECK(status IN ('active','disabled')),
            applied_event_id TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES domain_learning_candidates(candidate_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS domain_learning_style_scope "
        "ON domain_learning_style_items(bot_id,group_id,status,updated_at)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_learning_episodes (
            object_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL UNIQUE,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            situation TEXT NOT NULL,
            observed_context TEXT NOT NULL,
            action_taken TEXT NOT NULL,
            outcome_signal TEXT NOT NULL,
            reflection TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN
                ('dry_run','candidate','approved','enabled_for_prompt','disabled')),
            object_revision INTEGER NOT NULL CHECK(object_revision >= 1),
            applied_event_id TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES domain_learning_candidates(candidate_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS domain_learning_episodes_scope "
        "ON domain_learning_episodes(bot_id,group_id,state,updated_at)"
    )


def _create_domain_learning_failures_table(db: sqlite3.Connection) -> None:
    """Persist content-free domain extraction failures separately from candidates."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_learning_failures (
            result_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            domain TEXT NOT NULL CHECK(domain IN ('slang','style','episode')),
            extractor_version TEXT NOT NULL,
            source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
            error_code TEXT NOT NULL CHECK(error_code IN
                ('slang_key_collision','slang_stoplisted')),
            input_digest TEXT NOT NULL,
            result_digest TEXT NOT NULL,
            failure_revision INTEGER NOT NULL CHECK(failure_revision >= 1),
            created_at REAL NOT NULL,
            settled_at REAL,
            UNIQUE(bot_id,group_id,source_id,domain,extractor_version)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS domain_learning_failures_scope "
        "ON domain_learning_failures(bot_id,group_id,created_at,result_id)"
    )


def _upgrade_domain_learning_schema_19(db: sqlite3.Connection) -> None:
    """Add empty R19 tables without rewriting pre-existing N6 facts."""
    _create_domain_learning_schema(db)
    db.execute("PRAGMA user_version=19")


def _upgrade_temporal_trace_schema_20(db: sqlite3.Connection) -> None:
    """Add explicit fact-version edges without inferring legacy history."""
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(memory_facts)")}
    if "supersedes_fact_id" not in columns:
        db.execute("ALTER TABLE memory_facts ADD COLUMN supersedes_fact_id TEXT")
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_facts_supersedes "
        "ON memory_facts(supersedes_fact_id)"
    )
    db.execute("PRAGMA user_version=20")


def _upgrade_memory_fact_extraction_schema_21(db: sqlite3.Connection) -> None:
    """Add content-free, source-bound receipts for factual extraction results."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_fact_extraction_results (
            result_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            domain TEXT NOT NULL CHECK(domain='fact'),
            extractor_version TEXT NOT NULL,
            source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
            result_status TEXT NOT NULL CHECK(result_status IN ('candidates','no_evidence')),
            candidate_ids_json TEXT NOT NULL,
            result_digest TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(bot_id,group_id,source_id,domain,extractor_version)
        )
        """
    )
    db.execute("PRAGMA user_version=21")


def _upgrade_archive_scan_runs_schema_22(db: sqlite3.Connection) -> None:
    """Distinguish explicit bounded rescan checkpoints from incremental runs."""
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(archive_scan_runs)")}
    if "run_kind" not in columns:
        db.execute(
            "ALTER TABLE archive_scan_runs ADD COLUMN run_kind TEXT NOT NULL "
            "DEFAULT 'incremental' CHECK(run_kind IN ('incremental','rescan'))"
        )
    db.execute("PRAGMA user_version=22")


def _upgrade_domain_learning_failures_schema_23(db: sqlite3.Connection) -> None:
    """Add content-free per-domain failure records without rewriting candidates."""
    _create_domain_learning_failures_table(db)
    db.execute("PRAGMA user_version=23")


def _upgrade_archive_text_deadline_schema_24(db: sqlite3.Connection) -> None:
    """Persist the authoritative encrypted-text deadline on its source row."""
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(archive_sources)")}
    if "text_expires_at" not in columns:
        db.execute("ALTER TABLE archive_sources ADD COLUMN text_expires_at REAL")
    db.execute("PRAGMA user_version=24")


def _create_memory_extraction_runs_schema(db: sqlite3.Connection) -> None:
    """Create one bounded, content-free execution status per archive source."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_extraction_runs (
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
            run_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK(status IN
                ('running','complete','cancelled','unknown','abandoned')),
            stage TEXT NOT NULL CHECK(stage IN
                ('extracting','fact','slang','style','episode')),
            error_code TEXT NOT NULL DEFAULT '',
            started_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            finished_at REAL,
            PRIMARY KEY(bot_id,group_id,source_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS memory_extraction_runs_scope_status "
        "ON memory_extraction_runs(bot_id,group_id,status,updated_at,run_id)"
    )


def _upgrade_memory_extraction_runs_schema_25(db: sqlite3.Connection) -> None:
    """Persist current N6 extraction execution state without altering receipts."""
    _create_memory_extraction_runs_schema(db)
    db.execute("PRAGMA user_version=25")


def _create_social_experiences_schema(db: sqlite3.Connection) -> None:
    """Persist content-free, currently authorized N7 social experience links."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS social_experiences (
            experience_id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            platform_message_id TEXT NOT NULL CHECK(length(trim(platform_message_id)) > 0),
            observed_at REAL NOT NULL,
            reply_action_id TEXT NOT NULL,
            receipt TEXT NOT NULL CHECK(length(trim(receipt)) > 0),
            status TEXT NOT NULL CHECK(status IN ('active','invalidated')),
            UNIQUE(bot_id,group_id,episode_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS social_experiences_scope_status "
        "ON social_experiences(bot_id,group_id,status,observed_at,experience_id)"
    )


def _upgrade_social_experiences_schema_26(db: sqlite3.Connection) -> None:
    """Add identity-only SocialExperience facts without rewriting N6 or Story."""
    _create_social_experiences_schema(db)
    db.execute("PRAGMA user_version=26")


def _upgrade_social_story_schema_27(db: sqlite3.Connection) -> None:
    """Allow content-free Social fiction and link it to its current evidence row."""
    _create_story_schema(db)
    _create_story_events_table(db, "story_events_social_migration")
    columns = (
        "bot_id,event_id,arc_id,group_id,source_kind,origin_kind,author,event_type,"
        "variable_deltas,open_threads,resolve_threads,stage,arc_status,payload_digest,"
        "decision_digest,from_revision,to_revision,result_variables,result_open_threads,"
        "result_stage,result_status,life_updates,partner_updates,committed_at,commit_seq"
    )
    db.execute(
        "INSERT INTO story_events_social_migration (" + columns + ") "
        "SELECT " + columns + " FROM story_events"
    )
    db.execute("DROP TABLE story_events")
    db.execute("ALTER TABLE story_events_social_migration RENAME TO story_events")
    db.execute(
        "CREATE UNIQUE INDEX story_events_commit_seq ON story_events(commit_seq)"
    )
    db.execute(
        "CREATE INDEX story_events_arc_time "
        "ON story_events(bot_id,arc_id,committed_at,event_id)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS story_social_experience_links (
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            experience_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            arc_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(bot_id,group_id,experience_id),
            UNIQUE(bot_id,event_id),
            FOREIGN KEY(bot_id,event_id) REFERENCES story_events(bot_id,event_id),
            FOREIGN KEY(experience_id) REFERENCES social_experiences(experience_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS story_social_experience_links_event "
        "ON story_social_experience_links(bot_id,event_id)"
    )
    db.execute("PRAGMA user_version=27")


def _create_story_events_table(
    db: sqlite3.Connection, table_name: str, *, if_not_exists: bool = False
) -> None:
    if table_name not in {
        "story_events",
        "story_events_commit_seq_migration",
        "story_events_social_migration",
    }:
        raise ValueError("invalid Story event table name")
    conditional = "IF NOT EXISTS " if if_not_exists else ""
    db.execute(
        f"""
        CREATE TABLE {conditional}{table_name} (
            bot_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            arc_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            source_kind TEXT NOT NULL
                CHECK(source_kind IN ('admin_authored_fiction','social_fiction')),
            origin_kind TEXT NOT NULL DEFAULT 'admin_authored_fiction'
                CHECK(origin_kind IN ('admin_authored_fiction','storylet','dream_proposal',
                    'social_experience')),
            author TEXT NOT NULL,
            event_type TEXT NOT NULL,
            variable_deltas TEXT NOT NULL DEFAULT '{{}}',
            open_threads TEXT NOT NULL DEFAULT '[]',
            resolve_threads TEXT NOT NULL DEFAULT '[]',
            stage TEXT,
            arc_status TEXT CHECK(arc_status IS NULL OR arc_status IN ('active','closed')),
            payload_digest TEXT NOT NULL,
            decision_digest TEXT NOT NULL,
            from_revision INTEGER NOT NULL CHECK(from_revision >= 0),
            to_revision INTEGER NOT NULL CHECK(to_revision > from_revision),
            result_variables TEXT NOT NULL DEFAULT '{{}}',
            result_open_threads TEXT NOT NULL DEFAULT '[]',
            result_stage TEXT NOT NULL DEFAULT 'active',
            result_status TEXT NOT NULL DEFAULT 'active'
                CHECK(result_status IN ('active','closed')),
            life_updates TEXT NOT NULL DEFAULT '[]',
            partner_updates TEXT NOT NULL DEFAULT '[]',
            committed_at REAL NOT NULL,
            commit_seq INTEGER NOT NULL CHECK(commit_seq > 0),
            PRIMARY KEY(bot_id,event_id),
            FOREIGN KEY(bot_id,arc_id) REFERENCES story_arcs(bot_id,arc_id)
        )
        """
    )


def _ensure_story_event_commit_seq(db: sqlite3.Connection) -> None:
    event_columns = [
        str(row[1]) for row in db.execute("PRAGMA table_info(story_events)").fetchall()
    ]
    if "commit_seq" not in event_columns:
        _create_story_events_table(db, "story_events_commit_seq_migration")
        quoted_columns = ",".join(
            '"' + column.replace('"', '""') + '"' for column in event_columns
        )
        # Old events have no causal sequence.  Existing rowid is the migration's
        # deterministic best-effort ordering; it is not proof of causal order.
        db.execute(
            "INSERT INTO story_events_commit_seq_migration ("
            f"{quoted_columns},commit_seq) "
            f"SELECT {quoted_columns},ROW_NUMBER() OVER (ORDER BY rowid) "
            "FROM story_events ORDER BY rowid"
        )
        db.execute("DROP TABLE story_events")
        db.execute(
            "ALTER TABLE story_events_commit_seq_migration RENAME TO story_events"
        )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS story_events_commit_seq "
        "ON story_events(commit_seq)"
    )
    sequence_index = next(
        (
            row
            for row in db.execute("PRAGMA index_list(story_events)")
            if str(row[1]) == "story_events_commit_seq"
        ),
        None,
    )
    sequence_index_columns = tuple(
        str(row[2]) for row in db.execute("PRAGMA index_info(story_events_commit_seq)")
    )
    if (
        sequence_index is None
        or int(sequence_index[2]) != 1
        or int(sequence_index[4]) != 0
        or sequence_index_columns != ("commit_seq",)
    ):
        raise sqlite3.DatabaseError("invalid Story commit sequence index")


def _create_story_schema(db: sqlite3.Connection) -> None:
    """Create the N7 fiction-only StoryArc ledger inside the Store transaction.

    Story arcs are bot-level records.  ``story_arc_groups`` is the explicit
    presentation boundary: a group sees an arc only when that mapping exists.
    Events never contain raw chat, factual evidence, Canon, Persona, or
    transport fields.  The two fixed projection targets are intentionally
    narrow catch-up ledgers, not a general event bus.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS story_arcs (
            bot_id TEXT NOT NULL,
            arc_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('main','side','ambient')),
            title TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL DEFAULT 'active',
            status TEXT NOT NULL CHECK(status IN ('active','closed')),
            variables TEXT NOT NULL DEFAULT '{}',
            open_threads TEXT NOT NULL DEFAULT '[]',
            revision INTEGER NOT NULL CHECK(revision >= 0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(bot_id,arc_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS story_arc_groups (
            bot_id TEXT NOT NULL,
            arc_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            PRIMARY KEY(bot_id,arc_id,group_id),
            FOREIGN KEY(bot_id,arc_id) REFERENCES story_arcs(bot_id,arc_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS story_arc_groups_scope "
        "ON story_arc_groups(bot_id,group_id,arc_id)"
    )
    _create_story_events_table(db, "story_events", if_not_exists=True)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS story_projection_outbox (
            bot_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            arc_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            target TEXT NOT NULL CHECK(target IN ('life','partner')),
            effect_payload TEXT NOT NULL DEFAULT '{}',
            intent_digest TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','applied')),
            receipt_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(bot_id,event_id,target),
            FOREIGN KEY(bot_id,event_id) REFERENCES story_events(bot_id,event_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS story_projection_outbox_scope "
        "ON story_projection_outbox(bot_id,group_id,status,created_at,event_id,target)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS story_projection_receipts (
            receipt_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            target TEXT NOT NULL CHECK(target IN ('life','partner')),
            intent_digest TEXT NOT NULL,
            applied_at REAL NOT NULL,
            UNIQUE(bot_id,event_id,target),
            FOREIGN KEY(bot_id,event_id,target)
                REFERENCES story_projection_outbox(bot_id,event_id,target)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS story_projection_receipts_scope "
        "ON story_projection_receipts(bot_id,group_id,event_id,target)"
    )
    # Schema 10 already had the outbox/receipt tables.  N7-2 adds the
    # canonical typed effect payload without changing old rows into effects;
    # those rows remain pending and fail closed until their committed event
    # carries a real target effect.
    columns = {
        str(row[1])
        for row in db.execute("PRAGMA table_info(story_projection_outbox)").fetchall()
    }
    if "effect_payload" not in columns:
        db.execute(
            "ALTER TABLE story_projection_outbox ADD COLUMN "
            "effect_payload TEXT NOT NULL DEFAULT '{}'"
        )
    event_columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info(story_events)").fetchall()
    }
    if "origin_kind" not in event_columns:
        db.execute(
            "ALTER TABLE story_events ADD COLUMN origin_kind TEXT NOT NULL "
            "DEFAULT 'admin_authored_fiction' CHECK(origin_kind IN "
            "('admin_authored_fiction','storylet','dream_proposal'))"
        )
    if "life_updates" not in event_columns:
        db.execute("ALTER TABLE story_events ADD COLUMN life_updates TEXT NOT NULL DEFAULT '[]'")
    if "partner_updates" not in event_columns:
        db.execute("ALTER TABLE story_events ADD COLUMN partner_updates TEXT NOT NULL DEFAULT '[]'")
    _ensure_story_event_commit_seq(db)
    db.execute(
        "CREATE INDEX IF NOT EXISTS story_events_arc_time "
        "ON story_events(bot_id,arc_id,committed_at,event_id)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS story_life_states (
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            event_id TEXT NOT NULL,
            expires_at REAL NOT NULL CHECK(expires_at > 0),
            event_committed_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            PRIMARY KEY(bot_id,group_id,key)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS story_life_states_scope_expiry "
        "ON story_life_states(bot_id,group_id,expires_at,key)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS story_partner_states (
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            identity_kind TEXT NOT NULL DEFAULT 'fiction'
                CHECK(identity_kind='fiction'),
            display_name TEXT NOT NULL DEFAULT '',
            pinned_profile TEXT NOT NULL DEFAULT '',
            mood TEXT NOT NULL DEFAULT '',
            availability TEXT NOT NULL DEFAULT '',
            current_state TEXT NOT NULL DEFAULT '',
            constraints TEXT NOT NULL DEFAULT '[]',
            note TEXT NOT NULL DEFAULT '',
            event_note TEXT NOT NULL DEFAULT '',
            last_event_id TEXT NOT NULL DEFAULT '',
            last_event_at REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(bot_id,group_id,entity_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS story_partner_states_scope "
        "ON story_partner_states(bot_id,group_id,entity_id)"
    )
    life_state_columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info(story_life_states)").fetchall()
    }
    if "event_committed_at" not in life_state_columns:
        db.execute(
            "ALTER TABLE story_life_states ADD COLUMN event_committed_at REAL NOT NULL DEFAULT 0"
        )
    partner_state_columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info(story_partner_states)").fetchall()
    }
    if "last_event_at" not in partner_state_columns:
        db.execute(
            "ALTER TABLE story_partner_states ADD COLUMN last_event_at REAL NOT NULL DEFAULT 0"
        )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS storylet_states (
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            arc_id TEXT NOT NULL,
            registry_fingerprint TEXT NOT NULL,
            state_json TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 0),
            updated_at REAL NOT NULL,
            PRIMARY KEY(bot_id,group_id,arc_id),
            FOREIGN KEY(bot_id,arc_id) REFERENCES story_arcs(bot_id,arc_id)
        )
        """
    )


def _create_dream_schema(db: sqlite3.Connection) -> None:
    """Create the N7 Dream proposal/decision/commit ledger.

    A Dream proposal is an immutable candidate.  Only the StoryArc owner may
    move it through the decision columns and attach an already committed
    fiction event; this table never stores chat or factual source material.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS dream_proposals (
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            proposal_id TEXT NOT NULL,
            target_arc_id TEXT NOT NULL,
            target_arc_revision INTEGER NOT NULL CHECK(target_arc_revision >= 0),
            source_fingerprint TEXT NOT NULL,
            created_at REAL NOT NULL,
            proposal_json TEXT NOT NULL,
            proposal_digest TEXT NOT NULL,
            decision_status TEXT NOT NULL CHECK(decision_status IN ('pending','validated','rejected')),
            decision_digest TEXT,
            decided_at REAL,
            reason TEXT,
            committed_event_id TEXT,
            committed_at REAL,
            PRIMARY KEY(bot_id,group_id,proposal_id),
            FOREIGN KEY(bot_id,target_arc_id) REFERENCES story_arcs(bot_id,arc_id),
            FOREIGN KEY(bot_id,committed_event_id) REFERENCES story_events(bot_id,event_id),
            CHECK(
                (decision_status='pending'
                    AND decision_digest IS NULL
                    AND decided_at IS NULL
                    AND reason IS NULL
                    AND committed_event_id IS NULL
                    AND committed_at IS NULL)
                OR (decision_status='validated'
                    AND decision_digest IS NOT NULL
                    AND decided_at IS NOT NULL
                    AND ((committed_event_id IS NULL AND committed_at IS NULL)
                         OR (committed_event_id IS NOT NULL AND committed_at IS NOT NULL)))
                OR (decision_status='rejected'
                    AND decision_digest IS NOT NULL
                    AND decided_at IS NOT NULL
                    AND reason IS NOT NULL
                    AND length(trim(reason)) > 0
                    AND committed_event_id IS NULL
                    AND committed_at IS NULL)
            )
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS dream_proposals_scope_status "
        "ON dream_proposals(bot_id,group_id,decision_status,created_at,proposal_id)"
    )


def _create_schedule_schema(db: sqlite3.Connection) -> None:
    """Create the opt-in N7 daily-clock tables.

    Schedule owns only a per-scope clock and one immutable committed day
    document per local date.  Slots stay in a bounded JSON document so a
    partially written plan cannot be observed as a set of independent rows.
    The table contains no message, user, Social, or private-memory fields.
    """
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_clocks (
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            timezone TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 0),
            last_day TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL,
            PRIMARY KEY(bot_id,group_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_days (
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            local_day TEXT NOT NULL,
            timezone TEXT NOT NULL,
            day_id TEXT NOT NULL,
            input_digest TEXT NOT NULL,
            summary TEXT NOT NULL,
            slots TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(bot_id,group_id,local_day),
            UNIQUE(bot_id,group_id,day_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS schedule_days_scope "
        "ON schedule_days(bot_id,group_id,local_day)"
    )


def _validate_climate_key(key: tuple[str, str, str]) -> None:
    if len(key) != 3 or any(
        not part or part != part.strip() or len(part) > 64
        for part in key
    ):
        raise OperationError("invalid_climate_key")


def _climate_float(value: object, *, bounded: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise OperationError("invalid_climate_state")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_climate_state") from exc
    if not math.isfinite(number):
        raise OperationError("invalid_climate_state")
    if bounded and not 0.0 <= number <= 1.0:
        raise OperationError("invalid_climate_state")
    return number


def _climate_identity(value: object, *, limit: int, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > limit:
        raise OperationError(code)
    return value


def _climate_refs(values: Sequence[str], *, limit: int) -> tuple[str, ...]:
    if len(values) > limit:
        raise OperationError("invalid_climate_state")
    normalized: list[str] = []
    for value in values:
        normalized_value = _climate_identity(value, limit=128, code="invalid_climate_source")
        if normalized_value not in normalized:
            normalized.append(normalized_value)
    return tuple(normalized)


def _decode_climate_refs(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise OperationError("invalid_climate_state")
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise OperationError("invalid_climate_state") from exc
    if not isinstance(decoded, list):
        raise OperationError("invalid_climate_state")
    decoded_items = cast(list[object], decoded)
    refs: list[str] = []
    for item in decoded_items:
        if not isinstance(item, str):
            raise OperationError("invalid_climate_state")
        refs.append(item)
    return _climate_refs(refs, limit=256)


def _settings_json(document: dict[str, JsonValue]) -> str:
    """Validate a storage document and serialize it without non-standard JSON values."""
    try:
        validated = _JSON_DOCUMENT.validate_python(document, strict=True)
        return json.dumps(validated, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, ValidationError) as exc:
        raise OperationError("invalid_settings") from exc


def _settings_document(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, str):
        raise OperationError("invalid_settings")
    try:
        return _JSON_DOCUMENT.validate_json(value, strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise OperationError("invalid_settings") from exc


def _settings_row(db: sqlite3.Connection, revision: int | None = None) -> sqlite3.Row | None:
    if revision is None:
        return db.execute(
            "SELECT revision,document FROM config_versions ORDER BY revision DESC LIMIT 1"
        ).fetchone()
    return db.execute(
        "SELECT revision,document FROM config_versions WHERE revision=?", (revision,)
    ).fetchone()


def _settings_result(row: sqlite3.Row) -> tuple[int, dict[str, JsonValue]]:
    revision = row["revision"]
    if type(revision) is not int or revision < 1:
        raise OperationError("invalid_settings")
    return revision, _settings_document(row["document"])


def _archive_source_result(row: sqlite3.Row) -> dict[str, object]:
    status = row["status"]
    body_omitted = row["body_omitted"]
    source_revision = row["source_revision"]
    observed_at = row["observed_at"]
    ingested_at = row["ingested_at"]
    text_expires_at = row["text_expires_at"]
    if (
        not isinstance(status, str)
        or status not in _ARCHIVE_SOURCE_STATUSES
        or type(body_omitted) is not int
        or body_omitted not in {0, 1}
        or type(source_revision) is not int
        or source_revision < 1
        or not isinstance(observed_at, (int, float))
        or isinstance(observed_at, bool)
        or not math.isfinite(float(observed_at))
        or not isinstance(ingested_at, (int, float))
        or isinstance(ingested_at, bool)
        or not math.isfinite(float(ingested_at))
        or (
            text_expires_at is not None
            and (
                not isinstance(text_expires_at, (int, float))
                or isinstance(text_expires_at, bool)
                or not math.isfinite(float(text_expires_at))
                or float(text_expires_at) <= 0
            )
        )
    ):
        raise OperationError("invalid_archive_source")
    return {
        "source_id": str(row["source_id"]),
        "bot_id": str(row["bot_id"]),
        "group_id": str(row["group_id"]),
        "origin_event_id": str(row["origin_event_id"]),
        "platform_message_id": str(row["platform_message_id"]),
        "source_kind": str(row["source_kind"]),
        "speaker_kind": str(row["speaker_kind"]),
        "speaker_id": str(row["speaker_id"]),
        "observed_at": float(observed_at),
        "ingested_at": float(ingested_at),
        "text_expires_at": (
            None if text_expires_at is None else float(text_expires_at)
        ),
        "content_digest": row["content_digest"],
        "body_omitted": bool(body_omitted),
        "source_revision": source_revision,
        "status": status,
        "visibility_grant_id": str(row["visibility_grant_id"]),
    }


def _archive_run_result(row: sqlite3.Row) -> dict[str, object]:
    status = row["status"]
    if not isinstance(status, str) or status not in _ARCHIVE_RUN_STATUSES:
        raise OperationError("invalid_archive_run")
    integer_fields = (
        "scanned_count",
        "emitted_count",
        "committed_count",
        "last_page_committed_count",
    )
    for field in integer_fields:
        value = row[field]
        if type(value) is not int or value < 0:
            raise OperationError("invalid_archive_run")
    return {
        "run_id": str(row["run_id"]),
        "scanner": str(row["scanner"]),
        "bot_id": str(row["bot_id"]),
        "group_id": str(row["group_id"]),
        "scanner_version": str(row["scanner_version"]),
        "params_hash": str(row["params_hash"]),
        "from_marker": str(row["from_marker"]),
        "to_marker": str(row["to_marker"]),
        "last_committed_marker": str(row["last_committed_marker"]),
        "last_page_from_marker": str(row["last_page_from_marker"]),
        "last_page_to_marker": str(row["last_page_to_marker"]),
        "last_page_digest": str(row["last_page_digest"]),
        "last_page_committed_count": int(row["last_page_committed_count"]),
        "scanned_count": int(row["scanned_count"]),
        "emitted_count": int(row["emitted_count"]),
        "committed_count": int(row["committed_count"]),
        "status": status,
        "error_code": str(row["error_code"]),
        "started_at": float(row["started_at"]),
        "finished_at": None if row["finished_at"] is None else float(row["finished_at"]),
    }


def _archive_cursor_result(row: sqlite3.Row) -> dict[str, object]:
    status = row["status"]
    if not isinstance(status, str) or status not in _ARCHIVE_RUN_STATUSES:
        raise OperationError("invalid_archive_cursor")
    last_observed_at = row["last_observed_at"]
    if last_observed_at is not None:
        if not isinstance(last_observed_at, (int, float)) or isinstance(last_observed_at, bool):
            raise OperationError("invalid_archive_cursor")
        last_observed_at = float(last_observed_at)
    updated_at = row["updated_at"]
    if not isinstance(updated_at, (int, float)) or isinstance(updated_at, bool):
        raise OperationError("invalid_archive_cursor")
    return {
        "scanner": str(row["scanner"]),
        "bot_id": str(row["bot_id"]),
        "group_id": str(row["group_id"]),
        "scanner_version": str(row["scanner_version"]),
        "params_hash": str(row["params_hash"]),
        "last_committed_marker": str(row["last_committed_marker"]),
        "last_observed_at": last_observed_at,
        "status": status,
        "updated_at": float(updated_at),
    }


def _archive_source_values_match(row: sqlite3.Row, values: Mapping[str, object]) -> bool:
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
            "visibility_grant_id",
        )
    )


def payload_digest(values: Mapping[str, object]) -> str:
    """Hash a canonical, content-free event payload summary for conflict audit."""
    encoded = json.dumps(
        dict(values),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record_payload_conflict(
    db: sqlite3.Connection,
    *,
    kind: str,
    identity: str,
    revision: int,
    reason: str,
    stored_digest: str,
    incoming_digest: str,
) -> None:
    """Persist one deduplicated, content-free record of an identity collision."""
    details = json.dumps(
        {
            "incoming_digest": incoming_digest,
            "reason": reason,
            "stored_digest": stored_digest,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    exists = db.execute(
        "SELECT 1 FROM audit WHERE kind=? AND identity=? AND code='payload_conflict' "
        "AND details=? LIMIT 1",
        (kind, identity, details),
    ).fetchone()
    if exists is None:
        db.execute(
            "INSERT INTO audit(kind,identity,revision,code,details) VALUES (?,?,?,?,?)",
            (kind, identity, revision, "payload_conflict", details),
        )


def _archive_tombstone_result(row: sqlite3.Row) -> dict[str, object]:
    source_revision = row["source_revision"]
    revoked_at = row["revoked_at"]
    if (
        type(source_revision) is not int
        or source_revision < 1
        or not isinstance(revoked_at, (int, float))
        or isinstance(revoked_at, bool)
        or not math.isfinite(float(revoked_at))
        or not isinstance(row["reason"], str)
        or not isinstance(row["actor"], str)
    ):
        raise OperationError("invalid_archive_tombstone")
    return {
        "source_id": str(row["source_id"]),
        "bot_id": str(row["bot_id"]),
        "group_id": str(row["group_id"]),
        "speaker_id": str(row["speaker_id"]),
        "source_revision": source_revision,
        "reason": str(row["reason"]),
        "revoked_at": float(revoked_at),
        "actor": str(row["actor"]),
    }


def _archive_insert_source(
    db: sqlite3.Connection, values: Mapping[str, object]
) -> tuple[dict[str, object], bool]:
    tombstone = db.execute(
        "SELECT * FROM archive_source_tombstones WHERE source_id=?",
        (values["source_id"],),
    ).fetchone()
    if tombstone is not None and (
        tombstone["bot_id"] != values["bot_id"]
        or tombstone["group_id"] != values["group_id"]
    ):
        raise OperationError("archive_source_conflict")
    existing = db.execute(
        "SELECT * FROM archive_sources "
        "WHERE bot_id=? AND group_id=? AND origin_event_id=?",
        (values["bot_id"], values["group_id"], values["origin_event_id"]),
    ).fetchone()
    if existing is not None:
        if not _archive_source_values_match(existing, values):
            raise OperationError("archive_source_conflict")
        current_revision = int(existing["source_revision"])
        incoming_revision = int(cast(int, values["source_revision"]))
        if existing["status"] == "revoked" or tombstone is not None:
            if incoming_revision > current_revision:
                raise OperationError("archive_source_conflict")
            if tombstone is not None:
                tombstone_revision = int(tombstone["source_revision"])
                current_revision = max(current_revision, tombstone_revision)
                if existing["status"] != "revoked" or int(existing["source_revision"]) != current_revision:
                    db.execute(
                        "UPDATE archive_sources SET status='revoked',source_revision=? "
                        "WHERE source_id=?",
                        (current_revision, existing["source_id"]),
                    )
                    existing = db.execute(
                        "SELECT * FROM archive_sources WHERE source_id=?",
                        (existing["source_id"],),
                    ).fetchone()
                    if existing is None:
                        raise OperationError("invalid_archive_source")
                if current_revision > tombstone_revision:
                    db.execute(
                        "UPDATE archive_source_tombstones SET source_revision=? WHERE source_id=?",
                        (current_revision, values["source_id"]),
                    )
            return _archive_source_result(existing), False
        if incoming_revision != current_revision:
            raise OperationError("archive_source_conflict")
        return _archive_source_result(existing), False
    status = "active"
    source_revision = int(cast(int, values["source_revision"]))
    if tombstone is not None:
        status = "revoked"
        tombstone_speaker = str(tombstone["speaker_id"])
        if tombstone_speaker and tombstone_speaker != values["speaker_id"]:
            raise OperationError("archive_source_conflict")
        source_revision = max(source_revision, int(tombstone["source_revision"]))
        if source_revision != int(tombstone["source_revision"]):
            db.execute(
                "UPDATE archive_source_tombstones SET source_revision=? WHERE source_id=?",
                (source_revision, values["source_id"]),
            )
    db.execute(
        "INSERT INTO archive_sources("
        "source_id,bot_id,group_id,origin_event_id,platform_message_id,source_kind,"
        "speaker_kind,speaker_id,observed_at,ingested_at,content_digest,body_omitted,"
        "source_revision,status,visibility_grant_id,text_expires_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            values["source_id"],
            values["bot_id"],
            values["group_id"],
            values["origin_event_id"],
            values["platform_message_id"],
            values["source_kind"],
            values["speaker_kind"],
            values["speaker_id"],
            values["observed_at"],
            values["ingested_at"],
            values["content_digest"],
            values["body_omitted"],
            source_revision,
            status,
            values["visibility_grant_id"],
            values.get("text_expires_at"),
        ),
    )
    inserted = db.execute(
        "SELECT * FROM archive_sources WHERE source_id=?", (values["source_id"],)
    ).fetchone()
    if inserted is None:
        raise OperationError("invalid_archive_source")
    return _archive_source_result(inserted), True


def _memory_revocation_id(source_id: str, source_revision: int) -> str:
    material = f"{source_id}\x00{source_revision}".encode()
    return "memrev_" + hashlib.sha256(material).hexdigest()


def _insert_memory_revocation(
    db: sqlite3.Connection,
    *,
    bot_id: str,
    group_id: str,
    source_id: str,
    source_revision: int,
    reason: str,
    actor: str,
    now: float,
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO memory_revocations("
        "revocation_id,bot_id,group_id,source_id,source_revision,reason,actor,status,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            _memory_revocation_id(source_id, source_revision),
            bot_id,
            group_id,
            source_id,
            source_revision,
            reason,
            actor,
            "pending",
            now,
            now,
        ),
    )


def _backfill_memory_revocations(db: sqlite3.Connection) -> None:
    """Recreate pending propagation work from the durable source tombstones.

    Schema 8 already retained the source identity and tombstone.  A schema
    upgrade must therefore not silently lose an old revoke just because the
    memory propagation table is new in schema 9.  This helper copies only the
    identity-only tombstone fields and uses the same deterministic id and
    INSERT-OR-IGNORE path as a live revoke.
    """
    rows = db.execute(
        "SELECT source_id,bot_id,group_id,source_revision,reason,revoked_at,actor "
        "FROM archive_source_tombstones ORDER BY source_id"
    ).fetchall()
    for row in rows:
        _insert_memory_revocation(
            db,
            bot_id=str(row["bot_id"]),
            group_id=str(row["group_id"]),
            source_id=str(row["source_id"]),
            source_revision=int(row["source_revision"]),
            reason=str(row["reason"]),
            actor=str(row["actor"]),
            now=float(row["revoked_at"]),
        )


class Store:
    def __init__(self, path: Path, *, instance_id: str | None = None, bot_id: str | None = None) -> None:
        self.path = path.resolve()
        self.instance_id, self.bot_id = instance_id, bot_id
        self._lock = asyncio.Lock()
        self._connection: sqlite3.Connection | None = None
        self._owner: IO[bytes] | None = None
        self.storage_errors = 0

    async def transaction(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        async with self._lock:

            def work() -> T:
                if self._connection is None:
                    raise OperationError("store_closed")
                with self._connection:
                    return operation(self._connection)

            task = asyncio.create_task(asyncio.to_thread(work))
            try:
                return await drain_on_cancel(task)
            except sqlite3.Error as exc:
                self.storage_errors += 1
                raise OperationError("storage_unavailable") from exc

    async def qq_database_id(self) -> str:
        return await self.transaction(lambda db: str(db.execute(
            "SELECT database_id FROM qq_delivery_metadata WHERE id=1",
        ).fetchone()[0]))

    async def qq_ensure_account(self, account_id: str, *, now: float | None = None) -> QQStateRow:
        observed_at = time.time() if now is None else now
        return await self.transaction(lambda db: _qq_recover_account(db, account_id, observed_at))

    async def qq_delivery_snapshot(
        self, account_id: str, scope_key: QQScopeKey | None = None, *, now: float | None = None,
    ) -> QQDeliverySnapshot:
        observed_at = time.time() if now is None else now
        return await self.transaction(lambda db: self.qq_delivery_snapshot_transaction(
            db, account_id, scope_key, now=observed_at,
        ))

    @staticmethod
    def qq_delivery_snapshot_transaction(
        db: StoreConnection, account_id: str, scope_key: QQScopeKey | None = None, *, now: float,
    ) -> QQDeliverySnapshot:
        account_row = db.execute(
            "SELECT * FROM qq_delivery_state WHERE account_id=? AND scope_key=''", (account_id,),
        ).fetchone()
        if account_row is None:
            raise OperationError("qq_account_uninitialized")
        account = _qq_state_row(account_row)
        target = None if scope_key is None else _qq_ensure_state(db, account_id, scope_key, now)
        targets = tuple(_qq_state_row(row) for row in db.execute(
            "SELECT * FROM qq_delivery_state WHERE account_id=? AND scope_key<>'' ORDER BY scope_key",
            (account_id,),
        ))
        rows = _qq_action_rows(db, account_id, now)
        recent = [row for row in rows if float(row["budget_anchor"]) > now - 86400
                  or row["state"] == "dispatching"]
        account_attempts = tuple(QQQuotaAttempt(
            str(row["id"]), max(float(row["budget_anchor"]), now)
            if row["state"] == "dispatching" else float(row["budget_anchor"]), int(row["cost"]),
        ) for row in recent)
        target_attempts = tuple(QQQuotaAttempt(
            str(row["id"]), max(float(row["budget_anchor"]), now)
            if row["state"] == "dispatching" else float(row["budget_anchor"]), int(row["cost"]),
        ) for row in recent if scope_key is not None and _qq_matches_scope(row, scope_key))
        quota = QQQuotaProjection(
            observed_at=now,
            account_hour_used=sum(item.cost for item in account_attempts if item.budget_anchor > now - 3600),
            account_day_used=sum(item.cost for item in account_attempts),
            target_hour_used=sum(item.cost for item in target_attempts if item.budget_anchor > now - 3600),
            target_day_used=sum(item.cost for item in target_attempts),
            account_attempts=account_attempts, target_attempts=target_attempts,
        )
        latest = max((float(row["budget_anchor"]) for row in rows), default=0.0)
        settlements = [
            item.last_settled_at for item in (account, *targets) if item.last_settled_at is not None
        ]
        return QQDeliverySnapshot(
            account=account, target=target, targets=targets, quota=quota,
            unsettled_action_ids=tuple(str(row["id"]) for row in rows if row["state"] == "dispatching"),
            unreviewed_unknown_action_ids=tuple(
                str(row["id"]) for row in rows if row["state"] == "unknown"
                and not _qq_unknown_reviewed(row, account_id) and _qq_matches_scope(row, scope_key)
            ),
            clock_inconsistent=latest > now or any(value > now for value in settlements),
        )

    @staticmethod
    def qq_consume_transaction(
        db: StoreConnection, *, action_id: str, spec: QQWriteSpec,
        expected_account_revision: int, expected_target_revision: int,
        limits: QQDeliveryLimits, now: float, governor_revision: int | None = None,
    ) -> QQQuotaProjection:
        """Attach cost to the caller's dispatch intent, inside its same savepoint."""
        row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None or row["state"] != "dispatching" or row["qq_account_id"] is not None:
            raise OperationError("qq_intent_conflict")
        if row["bot_id"] != spec.account_id or not _qq_matches_scope(row, spec.scope_key) or spec.cost != 1:
            raise OperationError("qq_spec_mismatch")
        expected_api = ({
            "message.reply": "send_group_msg" if spec.scope_key[1] == "group" else "send_private_msg",
            "message.sticker": "send_group_msg" if spec.scope_key[1] == "group" else "send_private_msg",
            "manage_group": "set_group_ban", "poke": "send_poke", "reaction": "set_msg_emoji_like",
        }).get(str(row["action"]))
        if spec.api != expected_api:
            raise OperationError("qq_action_api_mismatch")
        snapshot = Store.qq_delivery_snapshot_transaction(db, spec.account_id, spec.scope_key, now=now)
        assert snapshot.target is not None
        if (snapshot.account.revision != expected_account_revision
                or snapshot.target.revision != expected_target_revision):
            raise OperationError("qq_revision_conflict")
        if snapshot.account.held or snapshot.target.held:
            raise OperationError("qq_held")
        if snapshot.clock_inconsistent:
            raise OperationError("qq_clock_inconsistent")
        # A newly inserted original intent has no QQ columns yet. Exclude that
        # exact row from the conservative legacy projection before committing it.
        account_attempts = tuple(
            item for item in snapshot.quota.account_attempts if item.action_id != action_id
        )
        target_attempts = tuple(
            item for item in snapshot.quota.target_attempts if item.action_id != action_id
        )
        for attempts, hour_limit, day_limit in (
            (account_attempts, limits.account_hour_limit, limits.account_day_limit),
            (target_attempts, limits.target_hour_limit, limits.target_day_limit),
        ):
            hour_used = sum(item.cost for item in attempts if item.budget_anchor > now - 3600)
            day_used = sum(item.cost for item in attempts)
            if hour_used + spec.cost > hour_limit or day_used + spec.cost > day_limit:
                raise OperationError("qq_capacity_exhausted")
        for state, interval in ((snapshot.account, limits.account_min_interval),
                                (snapshot.target, limits.target_min_interval)):
            if state.last_settled_at is not None and now < state.last_settled_at + interval:
                raise OperationError("qq_capacity_exhausted")
        if any(key != action_id for key in snapshot.unsettled_action_ids):
            raise OperationError("qq_slot_busy")
        db.execute(
            "UPDATE actions SET qq_account_id=?,qq_cost=?,qq_committed_at=?,qq_budget_anchor=?,"
            "qq_governor_revision=?,qq_api=?,qq_params_hash=? WHERE id=?",
            (spec.account_id, spec.cost, now, now,
             expected_account_revision if governor_revision is None else governor_revision,
             spec.api, spec.params_hash, action_id),
        )
        return Store.qq_delivery_snapshot_transaction(db, spec.account_id, spec.scope_key, now=now).quota

    @staticmethod
    def qq_settle_transaction(
        db: StoreConnection, *, action_id: str,
        state: Literal["succeeded", "failed", "cancelled_before_dispatch", "unknown"],
        code: str = "", receipt: str = "", now: float,
        hold_scope: Literal["account", "target"] | None = None, hold_reason: str = "",
        evidence: QQTransportEvidence | None = None,
    ) -> QQDeliverySnapshot:
        row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None or row["qq_account_id"] is None:
            raise OperationError("qq_intent_conflict")
        account_id = str(row["qq_account_id"])
        scope_kind = cast(Literal["group", "private"], row["scope_kind"])
        scope_key: QQScopeKey = (account_id, scope_kind, str(
            row["group_id"] if scope_kind == "group" else row["private_user_id"],
        ))
        if row["state"] != "dispatching":
            return Store.qq_delivery_snapshot_transaction(db, account_id, scope_key, now=now)
        anchor = max(float(row["qq_budget_anchor"]), now)
        db.execute(
            "UPDATE actions SET state=?,code=?,receipt=?,qq_budget_anchor=?,qq_settled_at=? WHERE id=?",
            (state, code or state, receipt, anchor, anchor, action_id),
        )
        for key in (None, scope_key):
            _qq_ensure_state(db, account_id, key, now)
            db.execute(
                "UPDATE qq_delivery_state SET last_settled_at=MAX(COALESCE(last_settled_at,?),?) "
                "WHERE account_id=? AND scope_key=?",
                (anchor, anchor, account_id, _qq_scope_encoded(account_id, key)),
            )
        if state == "unknown":
            hold_scope, hold_reason = "account", hold_reason or "unknown"
        if hold_scope is not None:
            key = None if hold_scope == "account" else scope_key
            current = _qq_ensure_state(db, account_id, key, now)
            _qq_hold_transaction(
                db, current, reason=hold_reason or code or state, actor="delivery", now=anchor,
                action_id=action_id, evidence=evidence,
            )
        return Store.qq_delivery_snapshot_transaction(db, account_id, scope_key, now=now)

    async def qq_hold(
        self, account_id: str, *, scope_key: QQScopeKey | None = None,
        expected_revision: int, reason: str, actor: str, now: float | None = None,
    ) -> QQStateRow:
        observed_at = time.time() if now is None else now

        def hold(db: StoreConnection) -> QQStateRow:
            snapshot = self.qq_delivery_snapshot_transaction(db, account_id, scope_key, now=observed_at)
            current = snapshot.account if scope_key is None else snapshot.target
            assert current is not None
            if current.revision != expected_revision:
                raise OperationError("qq_revision_conflict")
            return _qq_hold_transaction(db, current, reason=reason, actor=actor, now=observed_at)

        return await self.transaction(hold)

    async def qq_hold_transport(
        self, account_id: str, *, reason: str, actor: str = "transport",
    ) -> QQStateRow:
        """A trusted lifecycle observation freezes the current account atomically."""
        now = time.time()

        def hold(db: StoreConnection) -> QQStateRow:
            current = self.qq_delivery_snapshot_transaction(db, account_id, now=now).account
            return _qq_hold_transaction(db, current, reason=reason, actor=actor, now=now)

        return await self.transaction(hold)

    async def qq_begin_test_batch(
        self, account_id: str, *, batch_id: str, scope_key: QQScopeKey,
        write_limit: Literal[6, 12], expires_at: float, actor: str,
    ) -> None:
        now = time.time()

        def begin(db: StoreConnection) -> None:
            snapshot = self.qq_delivery_snapshot_transaction(db, account_id, scope_key, now=now)
            if not snapshot.account.held or snapshot.unsettled_action_ids:
                raise OperationError("qq_test_batch_not_held")
            identity = account_id + ":" + batch_id
            if db.execute(
                "SELECT 1 FROM audit WHERE kind='qq_test_batch' AND identity=?", (identity,),
            ).fetchone() is not None:
                raise OperationError("qq_test_batch_reused")
            db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details,created_at) "
                "VALUES ('qq_test_batch',?,?,'begun',?,?)",
                (identity, snapshot.account.revision, json.dumps({
                    "actor": actor, "batch_id": batch_id, "scope_key": scope_key,
                    "write_limit": write_limit, "expires_at": expires_at,
                }, separators=(",", ":")), now),
            )

        await self.transaction(begin)

    async def qq_resume(
        self, account_id: str, *, scope_key: QQScopeKey | None = None,
        expected_revision: int, reason: str, actor: str,
        reviewed_unknown_action_ids: tuple[str, ...] = (), now: float | None = None,
        test_batch_id: str | None = None,
    ) -> QQStateRow:
        observed_at = time.time() if now is None else now

        def resume(db: StoreConnection) -> QQStateRow:
            snapshot = self.qq_delivery_snapshot_transaction(db, account_id, scope_key, now=observed_at)
            current = snapshot.account if scope_key is None else snapshot.target
            assert current is not None
            if current.revision != expected_revision:
                raise OperationError("qq_revision_conflict")
            if snapshot.unsettled_action_ids:
                raise OperationError("qq_write_in_flight")
            if snapshot.clock_inconsistent:
                raise OperationError("qq_clock_inconsistent")
            unknown = set(snapshot.unreviewed_unknown_action_ids)
            reviewed = set(reviewed_unknown_action_ids)
            if unknown - reviewed:
                raise OperationError("qq_unknown_unreviewed")
            if reviewed - unknown or len(reviewed) != len(reviewed_unknown_action_ids):
                raise OperationError("qq_review_mismatch")
            if test_batch_id is not None:
                batch = db.execute(
                    "SELECT details FROM audit WHERE kind='qq_test_batch' AND identity=?",
                    (account_id + ":" + test_batch_id,),
                ).fetchone()
                if batch is None:
                    raise OperationError("qq_test_batch_invalid")
                batch_details = json.loads(str(batch["details"]))
                if float(batch_details["expires_at"]) <= observed_at:
                    raise OperationError("qq_test_batch_expired")
            details = json.dumps({
                "actor": actor, "scope_key": scope_key,
                "reviewed_unknown_action_ids": reviewed_unknown_action_ids, "test_batch_id": test_batch_id,
            }, separators=(",", ":"))
            audit = db.execute(
                "INSERT INTO audit(kind,identity,revision,code,details,created_at) "
                "VALUES ('qq_resume',?,?,?,?,?)",
                (account_id, current.revision + 1, reason, details, observed_at),
            )
            for action_id in reviewed_unknown_action_ids:
                db.execute("UPDATE actions SET qq_review_ref=? WHERE id=?", (audit.lastrowid, action_id))
            db.execute(
                "UPDATE qq_delivery_state SET held=0,revision=revision+1,reason=?,occurred_at=?,"
                "action_id='',audit_id=? WHERE account_id=? AND scope_key=? AND revision=?",
                ("test_batch_active:" + test_batch_id if test_batch_id is not None else reason,
                 observed_at, audit.lastrowid, account_id,
                 _qq_scope_encoded(account_id, scope_key), expected_revision),
            )
            return _qq_ensure_state(db, account_id, scope_key, observed_at)

        return await self.transaction(resume)

    async def archive_source_commit(
        self,
        *,
        source: Mapping[str, object],
        authorize: Callable[[sqlite3.Connection], None],
    ) -> dict[str, object]:
        """Commit one identity-only source after its caller's auth checks."""

        def commit(db: sqlite3.Connection) -> dict[str, object] | None:
            authorize(db)
            existing = db.execute(
                "SELECT * FROM archive_sources WHERE bot_id=? AND group_id=? "
                "AND origin_event_id=?",
                (source["bot_id"], source["group_id"], source["origin_event_id"]),
            ).fetchone()
            if existing is not None and not _archive_source_values_match(existing, source):
                digest_columns = (
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
                record_payload_conflict(
                    db,
                    kind="archive_source",
                    identity=str(existing["source_id"]),
                    revision=int(existing["source_revision"]),
                    reason="source_event_payload_mismatch",
                    stored_digest=payload_digest(
                        {column: existing[column] for column in digest_columns}
                    ),
                    incoming_digest=payload_digest(
                        {column: source[column] for column in digest_columns}
                    ),
                )
                return None
            record, _ = _archive_insert_source(db, source)
            return record

        record = await self.transaction(commit)
        if record is None:
            raise OperationError("payload_conflict")
        return record

    async def archive_source_revoke(
        self,
        *,
        source_id: str,
        bot_id: str,
        group_id: str,
        actor: str,
        reason: str,
        now: float,
        expected_revision: int | None,
        authorize: Callable[[sqlite3.Connection], None],
    ) -> dict[str, object]:
        """Commit a source revocation and its tombstone in one Store transaction."""

        def commit(db: sqlite3.Connection) -> dict[str, object]:
            authorize(db)
            source_row = db.execute(
                "SELECT * FROM archive_sources WHERE source_id=?", (source_id,)
            ).fetchone()
            tombstone_row = db.execute(
                "SELECT * FROM archive_source_tombstones WHERE source_id=?", (source_id,)
            ).fetchone()
            for row in (source_row, tombstone_row):
                if row is not None and (
                    row["bot_id"] != bot_id or row["group_id"] != group_id
                ):
                    raise OperationError("denied")

            source_revision = 0 if source_row is None else int(source_row["source_revision"])
            tombstone_revision = (
                0 if tombstone_row is None else int(tombstone_row["source_revision"])
            )
            current_revision = max(source_revision, tombstone_revision)
            if expected_revision is not None and expected_revision != current_revision:
                raise OperationError("revision_conflict")

            self.climate_invalidate_source_transaction(
                db, source_id=source_id, bot_id=bot_id, group_id=group_id,
                actor=actor, reason="archive_source_revoked",
            )

            if tombstone_row is not None and (
                source_row is None or source_row["status"] == "revoked"
            ):
                if source_row is not None and source_revision > tombstone_revision:
                    db.execute(
                        "UPDATE archive_source_tombstones SET source_revision=? "
                        "WHERE source_id=?",
                        (source_revision, source_id),
                    )
                    tombstone_row = db.execute(
                        "SELECT * FROM archive_source_tombstones WHERE source_id=?",
                        (source_id,),
                    ).fetchone()
                if tombstone_row is None:
                    raise OperationError("invalid_archive_tombstone")
                _insert_memory_revocation(
                    db,
                    bot_id=bot_id,
                    group_id=group_id,
                    source_id=source_id,
                    source_revision=int(tombstone_row["source_revision"]),
                    reason=str(tombstone_row["reason"]),
                    actor=str(tombstone_row["actor"]),
                    now=now,
                )
                return {
                    "source": None
                    if source_row is None
                    else _archive_source_result(source_row),
                    "tombstone": _archive_tombstone_result(tombstone_row),
                }

            next_revision = current_revision + 1
            if source_row is not None:
                db.execute(
                    "UPDATE archive_sources SET status='revoked',source_revision=? "
                    "WHERE source_id=?",
                    (next_revision, source_id),
                )
                source_row = db.execute(
                    "SELECT * FROM archive_sources WHERE source_id=?", (source_id,)
                ).fetchone()
                if source_row is None:
                    raise OperationError("invalid_archive_source")
                stored_speaker_id = str(source_row["speaker_id"])
            else:
                stored_speaker_id = ""

            if tombstone_row is None:
                db.execute(
                    "INSERT INTO archive_source_tombstones("
                    "source_id,bot_id,group_id,speaker_id,source_revision,reason,"
                    "revoked_at,actor) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        source_id,
                        bot_id,
                        group_id,
                        stored_speaker_id,
                        next_revision,
                        reason,
                        now,
                        actor,
                    ),
                )
            else:
                # An active source with a pre-existing tombstone is an old or
                # externally repaired state; preserve the first revocation facts.
                db.execute(
                    "UPDATE archive_source_tombstones SET source_revision=? "
                    "WHERE source_id=?",
                    (next_revision, source_id),
                )
            tombstone_row = db.execute(
                "SELECT * FROM archive_source_tombstones WHERE source_id=?", (source_id,)
            ).fetchone()
            if tombstone_row is None:
                raise OperationError("invalid_archive_tombstone")
            owner_row = source_row if source_row is not None else tombstone_row
            if owner_row is not None and owner_row["speaker_id"]:
                self.climate_invalidate_transaction(
                    db, (bot_id, group_id, str(owner_row["speaker_id"])),
                    actor=actor, reason="archive_source_revoked",
                )

            _insert_memory_revocation(
                db,
                bot_id=bot_id,
                group_id=group_id,
                source_id=source_id,
                source_revision=int(tombstone_row["source_revision"]),
                reason=str(tombstone_row["reason"]),
                actor=str(tombstone_row["actor"]),
                now=now,
            )
            return {
                "source": None
                if source_row is None
                else _archive_source_result(source_row),
                "tombstone": _archive_tombstone_result(tombstone_row),
            }

        return await self.transaction(commit)

    async def archive_source_status_read(
        self,
        *,
        source_id: str,
        bot_id: str,
        group_id: str,
        authorize: Callable[[sqlite3.Connection], None],
    ) -> dict[str, object | None]:
        """Read source identity and tombstone state through the Store executor."""

        def read(db: sqlite3.Connection) -> dict[str, object | None]:
            authorize(db)
            source_row = db.execute(
                "SELECT * FROM archive_sources WHERE source_id=?", (source_id,)
            ).fetchone()
            tombstone_row = db.execute(
                "SELECT * FROM archive_source_tombstones WHERE source_id=?", (source_id,)
            ).fetchone()
            for row in (source_row, tombstone_row):
                if row is not None and (
                    row["bot_id"] != bot_id or row["group_id"] != group_id
                ):
                    raise OperationError("denied")
            return {
                "source": None
                if source_row is None
                else _archive_source_result(source_row),
                "tombstone": None
                if tombstone_row is None
                else _archive_tombstone_result(tombstone_row),
            }

        return await self.transaction(read)

    async def archive_rescan_begin(
        self,
        *,
        run: Mapping[str, object],
        expected_cursor: Mapping[str, object],
        now: float,
        authorize: Callable[[sqlite3.Connection], None],
    ) -> dict[str, object]:
        """Create or resume one explicit rescan while preserving the source cursor."""

        if run.get("run_kind") != "rescan":
            raise OperationError("invalid_archive_run")
        if (
            run["from_marker"] == run["to_marker"]
            or run["to_marker"] != expected_cursor["last_committed_marker"]
        ):
            raise OperationError("archive_rescan_range_conflict")
        if (
            run["scanner_version"] == expected_cursor["scanner_version"]
            and run["params_hash"] == expected_cursor["params_hash"]
        ):
            raise OperationError("archive_rescan_not_needed")

        def begin(db: sqlite3.Connection) -> dict[str, object]:
            authorize(db)
            existing_run = db.execute(
                "SELECT * FROM archive_scan_runs WHERE run_id=?", (run["run_id"],)
            ).fetchone()
            cursor = db.execute(
                "SELECT * FROM archive_cursors WHERE scanner=? AND bot_id=? AND group_id=?",
                (run["scanner"], run["bot_id"], run["group_id"]),
            ).fetchone()
            if cursor is None:
                raise OperationError("archive_cursor_conflict")

            if existing_run is not None:
                if (
                    existing_run["run_kind"] != "rescan"
                    or any(
                        existing_run[column] != run[column]
                        for column in (
                            "scanner",
                            "bot_id",
                            "group_id",
                            "scanner_version",
                            "params_hash",
                            "from_marker",
                            "to_marker",
                        )
                    )
                ):
                    raise OperationError("archive_run_conflict")
                if existing_run["status"] == "committed":
                    if (
                        cursor["scanner_version"] != run["scanner_version"]
                        or cursor["params_hash"] != run["params_hash"]
                        or cursor["last_committed_marker"] != run["to_marker"]
                        or cursor["status"] != "committed"
                    ):
                        raise OperationError("archive_cursor_conflict")
                    return _archive_run_result(existing_run)

            if (
                expected_cursor["status"] != "needs_rescan"
                or cursor["scanner_version"] != expected_cursor["scanner_version"]
                or cursor["params_hash"] != expected_cursor["params_hash"]
                or cursor["last_committed_marker"]
                != expected_cursor["last_committed_marker"]
                or cursor["status"] != expected_cursor["status"]
            ):
                raise OperationError("archive_cursor_conflict")

            active_run = db.execute(
                "SELECT run_id FROM archive_scan_runs WHERE scanner=? AND bot_id=? "
                "AND group_id=? AND run_kind='rescan' AND status='running' "
                "ORDER BY started_at LIMIT 1",
                (run["scanner"], run["bot_id"], run["group_id"]),
            ).fetchone()
            if active_run is not None and active_run["run_id"] != run["run_id"]:
                raise OperationError("archive_rescan_in_progress")

            if existing_run is None:
                db.execute(
                    "INSERT INTO archive_scan_runs("
                    "run_id,scanner,bot_id,group_id,scanner_version,params_hash,run_kind,"
                    "from_marker,to_marker,last_committed_marker,status,started_at) "
                    "VALUES (?,?,?,?,?,?,'rescan',?,?,?,'running',?)",
                    (
                        run["run_id"],
                        run["scanner"],
                        run["bot_id"],
                        run["group_id"],
                        run["scanner_version"],
                        run["params_hash"],
                        run["from_marker"],
                        run["to_marker"],
                        run["from_marker"],
                        now,
                    ),
                )
            elif existing_run["status"] in {
                "abandoned",
                "cancelled",
                "failed",
                "needs_rescan",
            }:
                db.execute(
                    "UPDATE archive_scan_runs SET status='running',error_code='',"
                    "finished_at=NULL WHERE run_id=?",
                    (run["run_id"],),
                )
            elif existing_run["status"] != "running":
                raise OperationError("archive_run_not_running")

            result = db.execute(
                "SELECT * FROM archive_scan_runs WHERE run_id=?", (run["run_id"],)
            ).fetchone()
            if result is None:
                raise OperationError("invalid_archive_run")
            return _archive_run_result(result)

        return await self.transaction(begin)

    async def archive_rescan_page_commit(
        self,
        *,
        run: Mapping[str, object],
        expected_cursor: Mapping[str, object],
        sources: Sequence[Mapping[str, object]],
        markers: Sequence[str],
        next_marker: str,
        page_digest: str,
        high_water_confirmed: bool,
        now: float,
        authorize: Callable[[sqlite3.Connection], None],
    ) -> dict[str, object]:
        """Commit one rescan page without advancing the incremental cursor."""

        if len(sources) != len(markers) or run.get("run_kind") != "rescan":
            raise OperationError("invalid_archive_page")
        if type(high_water_confirmed) is not bool:
            raise OperationError("invalid_archive_page")
        if sources and next_marker != markers[-1]:
            raise OperationError("archive_marker_conflict")

        def commit(db: sqlite3.Connection) -> dict[str, object]:
            authorize(db)
            existing_run = db.execute(
                "SELECT * FROM archive_scan_runs WHERE run_id=?", (run["run_id"],)
            ).fetchone()
            if existing_run is None or existing_run["run_kind"] != "rescan":
                raise OperationError("archive_rescan_not_started")
            if any(
                existing_run[column] != run[column]
                for column in (
                    "scanner",
                    "bot_id",
                    "group_id",
                    "scanner_version",
                    "params_hash",
                    "to_marker",
                )
            ):
                raise OperationError("archive_run_conflict")
            if (
                existing_run["last_page_from_marker"] == run["from_marker"]
                and existing_run["last_page_to_marker"] == next_marker
                and existing_run["last_page_digest"] == page_digest
            ):
                return _archive_run_result(existing_run)
            if existing_run["status"] != "running":
                raise OperationError("archive_run_not_running")
            if existing_run["last_committed_marker"] != run["from_marker"]:
                raise OperationError("archive_cursor_conflict")

            cursor = db.execute(
                "SELECT * FROM archive_cursors WHERE scanner=? AND bot_id=? AND group_id=?",
                (run["scanner"], run["bot_id"], run["group_id"]),
            ).fetchone()
            if cursor is None or (
                expected_cursor["status"] != "needs_rescan"
                or cursor["scanner_version"] != expected_cursor["scanner_version"]
                or cursor["params_hash"] != expected_cursor["params_hash"]
                or cursor["last_committed_marker"]
                != expected_cursor["last_committed_marker"]
                or cursor["status"] != expected_cursor["status"]
                or run["to_marker"] != expected_cursor["last_committed_marker"]
            ):
                raise OperationError("archive_cursor_conflict")
            if not sources and not high_water_confirmed:
                raise OperationError("archive_empty_page_requires_scanner")
            final = next_marker == run["to_marker"]
            if high_water_confirmed and not final:
                raise OperationError("archive_marker_conflict")
            if final and not high_water_confirmed:
                raise OperationError("archive_rescan_high_water_requires_scanner")

            committed_now = 0
            for source in sources:
                _, inserted = _archive_insert_source(db, source)
                committed_now += int(inserted)
            status = "committed" if final else "running"
            scanned = int(existing_run["scanned_count"]) + len(sources)
            emitted = int(existing_run["emitted_count"]) + len(sources)
            committed = int(existing_run["committed_count"]) + committed_now
            if final:
                updated = db.execute(
                    "UPDATE archive_cursors SET scanner_version=?,params_hash=?,"
                    "status='committed',updated_at=? WHERE scanner=? AND bot_id=? "
                    "AND group_id=? AND scanner_version=? AND params_hash=? "
                    "AND last_committed_marker=? AND status='needs_rescan'",
                    (
                        run["scanner_version"],
                        run["params_hash"],
                        now,
                        run["scanner"],
                        run["bot_id"],
                        run["group_id"],
                        expected_cursor["scanner_version"],
                        expected_cursor["params_hash"],
                        expected_cursor["last_committed_marker"],
                    ),
                )
                if updated.rowcount != 1:
                    raise OperationError("archive_cursor_conflict")
            db.execute(
                "UPDATE archive_scan_runs SET last_committed_marker=?,"
                "last_page_from_marker=?,last_page_to_marker=?,last_page_digest=?,"
                "last_page_committed_count=?,scanned_count=?,emitted_count=?,"
                "committed_count=?,status=?,finished_at=? WHERE run_id=?",
                (
                    next_marker,
                    run["from_marker"],
                    next_marker,
                    page_digest,
                    committed_now,
                    scanned,
                    emitted,
                    committed,
                    status,
                    now if final else None,
                    run["run_id"],
                ),
            )
            result = db.execute(
                "SELECT * FROM archive_scan_runs WHERE run_id=?", (run["run_id"],)
            ).fetchone()
            if result is None:
                raise OperationError("invalid_archive_run")
            return _archive_run_result(result)

        return await self.transaction(commit)

    async def archive_backfill_commit(
        self,
        *,
        run: Mapping[str, object],
        sources: Sequence[Mapping[str, object]],
        markers: Sequence[str],
        next_marker: str,
        page_digest: str,
        high_water_confirmed: bool,
        now: float,
        authorize: Callable[[sqlite3.Connection], None],
    ) -> dict[str, object]:
        """Atomically append one bounded page and advance its opaque cursor.

        Empty pages can finish only at the fixed high-water marker after the
        scanner confirms that it reached that marker.
        """
        if len(sources) != len(markers) or run.get("run_kind") != "incremental":
            raise OperationError("invalid_archive_page")
        if type(high_water_confirmed) is not bool:
            raise OperationError("invalid_archive_page")
        if sources and next_marker != markers[-1]:
            raise OperationError("archive_marker_conflict")

        def commit(db: sqlite3.Connection) -> dict[str, object]:
            authorize(db)
            existing_run = db.execute(
                "SELECT * FROM archive_scan_runs WHERE run_id=?", (run["run_id"],)
            ).fetchone()
            if existing_run is not None:
                if any(
                    existing_run[column] != run[column]
                    for column in (
                        "scanner",
                        "bot_id",
                        "group_id",
                        "scanner_version",
                        "params_hash",
                        "run_kind",
                        "to_marker",
                    )
                ):
                    raise OperationError("archive_run_conflict")
                if (
                    existing_run["last_page_from_marker"] == run["from_marker"]
                    and existing_run["last_page_to_marker"] == next_marker
                    and existing_run["last_page_digest"] == page_digest
                ):
                    return _archive_run_result(existing_run)
                if existing_run["status"] not in {"running", "abandoned"}:
                    raise OperationError("archive_run_not_running")
                if (
                    existing_run["status"] == "abandoned"
                    and existing_run["last_committed_marker"] != run["from_marker"]
                ):
                    raise OperationError("archive_cursor_conflict")

            cursor = db.execute(
                "SELECT * FROM archive_cursors "
                "WHERE scanner=? AND bot_id=? AND group_id=?",
                (run["scanner"], run["bot_id"], run["group_id"]),
            ).fetchone()
            if cursor is None:
                if run["from_marker"] != "":
                    raise OperationError("archive_cursor_conflict")
                db.execute(
                    "INSERT INTO archive_cursors("
                    "scanner,bot_id,group_id,scanner_version,params_hash,"
                    "last_committed_marker,last_observed_at,status,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        run["scanner"],
                        run["bot_id"],
                        run["group_id"],
                        run["scanner_version"],
                        run["params_hash"],
                        "",
                        None,
                        "running",
                        now,
                    ),
                )
                cursor = db.execute(
                    "SELECT * FROM archive_cursors "
                    "WHERE scanner=? AND bot_id=? AND group_id=?",
                    (run["scanner"], run["bot_id"], run["group_id"]),
                ).fetchone()
            if cursor is None:
                raise OperationError("invalid_archive_cursor")
            if (
                cursor["scanner_version"] != run["scanner_version"]
                or cursor["params_hash"] != run["params_hash"]
            ):
                db.execute(
                    "UPDATE archive_cursors SET status='needs_rescan',updated_at=? "
                    "WHERE scanner=? AND bot_id=? AND group_id=?",
                    (now, run["scanner"], run["bot_id"], run["group_id"]),
                )
                return {
                    "run_id": str(run["run_id"]),
                    "status": "needs_rescan",
                    "cursor_marker": str(cursor["last_committed_marker"]),
                    "scanned_count": 0,
                    "emitted_count": 0,
                    "committed_count": 0,
                }
            if cursor["status"] == "needs_rescan":
                return {
                    "run_id": str(run["run_id"]),
                    "status": "needs_rescan",
                    "cursor_marker": str(cursor["last_committed_marker"]),
                    "scanned_count": 0,
                    "emitted_count": 0,
                    "committed_count": 0,
                }
            if cursor["last_committed_marker"] != run["from_marker"]:
                raise OperationError("archive_cursor_conflict")
            if not sources:
                if not high_water_confirmed:
                    raise OperationError("archive_empty_page_requires_scanner")
                if next_marker != run["to_marker"]:
                    raise OperationError("archive_marker_conflict")

            if existing_run is None:
                db.execute(
                    "INSERT INTO archive_scan_runs("
                    "run_id,scanner,bot_id,group_id,scanner_version,params_hash,run_kind,"
                    "from_marker,to_marker,last_committed_marker,status,started_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run["run_id"],
                        run["scanner"],
                        run["bot_id"],
                        run["group_id"],
                        run["scanner_version"],
                        run["params_hash"],
                        "incremental",
                        run["from_marker"],
                        run["to_marker"],
                        run["from_marker"],
                        "running",
                        now,
                    ),
                )
                existing_run = db.execute(
                    "SELECT * FROM archive_scan_runs WHERE run_id=?", (run["run_id"],)
                ).fetchone()
            if existing_run is None:
                raise OperationError("invalid_archive_run")

            committed_now = 0
            latest_observed = cursor["last_observed_at"]
            for source in sources:
                _, inserted = _archive_insert_source(db, source)
                committed_now += int(inserted)
                observed_at = source["observed_at"]
                if latest_observed is None or float(cast(float, observed_at)) > float(
                    cast(float, latest_observed)
                ):
                    latest_observed = observed_at
            page_scanned = len(sources)
            page_emitted = len(sources)
            final = next_marker == run["to_marker"]
            next_status = "committed" if final else "running"
            previous_scanned = int(existing_run["scanned_count"])
            previous_emitted = int(existing_run["emitted_count"])
            previous_committed = int(existing_run["committed_count"])
            db.execute(
                "UPDATE archive_cursors SET last_committed_marker=?,last_observed_at=?,"
                "status=?,updated_at=? WHERE scanner=? AND bot_id=? AND group_id=?",
                (
                    next_marker,
                    latest_observed,
                    next_status,
                    now,
                    run["scanner"],
                    run["bot_id"],
                    run["group_id"],
                ),
            )
            db.execute(
                "UPDATE archive_scan_runs SET last_committed_marker=?,"
                "last_page_from_marker=?,last_page_to_marker=?,last_page_digest=?,"
                "last_page_committed_count=?,scanned_count=?,emitted_count=?,"
                "committed_count=?,status=?,finished_at=? WHERE run_id=?",
                (
                    next_marker,
                    run["from_marker"],
                    next_marker,
                    page_digest,
                    committed_now,
                    previous_scanned + page_scanned,
                    previous_emitted + page_emitted,
                    previous_committed + committed_now,
                    next_status,
                    now if final else None,
                    run["run_id"],
                ),
            )
            result = db.execute(
                "SELECT * FROM archive_scan_runs WHERE run_id=?", (run["run_id"],)
            ).fetchone()
            if result is None:
                raise OperationError("invalid_archive_run")
            return _archive_run_result(result)

        return await self.transaction(commit)

    async def archive_cancel_run(
        self,
        run_id: str,
        *,
        bot_id: str,
        group_id: str,
        authorize: Callable[[sqlite3.Connection], None],
        now: float | None = None,
    ) -> dict[str, object]:
        """Cancel one exact-scope run after the caller's archive authorization."""
        timestamp = time.time() if now is None else now

        def cancel(db: sqlite3.Connection) -> dict[str, object]:
            row = db.execute(
                "SELECT * FROM archive_scan_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise OperationError("archive_run_missing")
            if row["bot_id"] != bot_id or row["group_id"] != group_id:
                raise OperationError("denied")
            authorize(db)
            if row["status"] == "running":
                db.execute(
                    "UPDATE archive_scan_runs SET status='cancelled',finished_at=? "
                    "WHERE run_id=? AND bot_id=? AND group_id=?",
                    (timestamp, run_id, bot_id, group_id),
                )
                row = db.execute(
                    "SELECT * FROM archive_scan_runs WHERE run_id=?", (run_id,)
                ).fetchone()
            if row is None:
                raise OperationError("invalid_archive_run")
            return _archive_run_result(row)

        return await self.transaction(cancel)

    async def archive_cursor_read(
        self,
        *,
        scanner: str,
        bot_id: str,
        group_id: str,
        authorize: Callable[[sqlite3.Connection], None],
    ) -> dict[str, object] | None:
        """Read one cursor through the same Store executor used for writes."""

        def read(db: sqlite3.Connection) -> dict[str, object] | None:
            authorize(db)
            row = db.execute(
                "SELECT * FROM archive_cursors WHERE scanner=? AND bot_id=? AND group_id=?",
                (scanner, bot_id, group_id),
            ).fetchone()
            return None if row is None else _archive_cursor_result(row)

        return await self.transaction(read)

    async def memory_extraction_run_begin(
        self,
        *,
        bot_id: str,
        group_id: str,
        source_id: str,
        source_revision: int,
    ) -> MemoryExtractionRun:
        """Start the bounded current run for one still-authorized source revision."""
        now = time.time()
        run_id = uuid.uuid4().hex

        def begin(db: sqlite3.Connection) -> MemoryExtractionRun:
            source = db.execute(
                "SELECT 1 FROM archive_sources AS s WHERE s.source_id=? AND s.bot_id=? "
                "AND s.group_id=? AND s.source_revision=? AND s.status='active' "
                "AND s.source_kind='human_message' AND s.speaker_kind='human' "
                "AND NOT EXISTS (SELECT 1 FROM archive_source_tombstones AS t "
                "WHERE t.source_id=s.source_id)",
                (source_id, bot_id, group_id, source_revision),
            ).fetchone()
            if source is None:
                raise OperationError("source_revoked")
            cursor = db.execute(
                "INSERT INTO memory_extraction_runs(bot_id,group_id,source_id,source_revision,"
                "run_id,status,stage,error_code,started_at,updated_at,finished_at) "
                "VALUES (?,?,?,?,?,'running','extracting','',?,?,NULL) "
                "ON CONFLICT(bot_id,group_id,source_id) DO UPDATE SET "
                "source_revision=excluded.source_revision,run_id=excluded.run_id,status='running',"
                "stage='extracting',error_code='',started_at=excluded.started_at,"
                "updated_at=excluded.updated_at,finished_at=NULL "
                "WHERE memory_extraction_runs.status<>'running'",
                (bot_id, group_id, source_id, source_revision, run_id, now, now),
            )
            if cursor.rowcount != 1:
                raise OperationError("memory_extraction_in_progress")
            row = db.execute(
                "SELECT * FROM memory_extraction_runs WHERE bot_id=? AND group_id=? AND source_id=?",
                (bot_id, group_id, source_id),
            ).fetchone()
            if row is None:
                raise OperationError("invalid_memory_extraction_run")
            return _memory_extraction_run_result(row)

        return await self.transaction(begin)

    async def memory_extraction_run_stage(
        self, run: MemoryExtractionRun, stage: str
    ) -> MemoryExtractionRun:
        """Advance a live run without changing its source or run identity."""
        if stage not in _MEMORY_EXTRACTION_RUN_STAGES:
            raise OperationError("invalid_memory_extraction_run")
        now = time.time()

        def advance(db: sqlite3.Connection) -> MemoryExtractionRun:
            cursor = db.execute(
                "UPDATE memory_extraction_runs SET stage=?,updated_at=? WHERE bot_id=? "
                "AND group_id=? AND source_id=? AND source_revision=? AND run_id=? AND status='running'",
                (
                    stage,
                    now,
                    run.bot_id,
                    run.group_id,
                    run.source_id,
                    run.source_revision,
                    run.run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise OperationError("memory_extraction_run_changed")
            row = db.execute(
                "SELECT * FROM memory_extraction_runs WHERE bot_id=? AND group_id=? AND source_id=?",
                (run.bot_id, run.group_id, run.source_id),
            ).fetchone()
            if row is None:
                raise OperationError("invalid_memory_extraction_run")
            return _memory_extraction_run_result(row)

        return await self.transaction(advance)

    async def memory_extraction_run_finish(
        self,
        run: MemoryExtractionRun,
        *,
        status: str,
        error_code: str = "",
        require_domains: bool = True,
        required_domains: Sequence[Literal["fact", "slang", "style", "episode"]] | None = None,
    ) -> MemoryExtractionRun:
        """Finish after the selected receipts commit; omitted selection keeps old semantics."""
        if status not in {"complete", "cancelled", "unknown"}:
            raise OperationError("invalid_memory_extraction_run")
        if len(error_code) > 64 or any(ord(char) < 32 for char in error_code):
            raise OperationError("invalid_memory_extraction_run")
        domains = (
            tuple(required_domains) if required_domains is not None
            else ("fact", "slang", "style", "episode") if require_domains else ("fact",)
        )
        if not set(domains) <= {"fact", "slang", "style", "episode"}:
            raise OperationError("invalid_memory_extraction_run")
        now = time.time()

        def finish(db: sqlite3.Connection) -> MemoryExtractionRun:
            if status == "complete" and "fact" in domains:
                fact = db.execute(
                    "SELECT 1 FROM memory_fact_extraction_results WHERE bot_id=? AND group_id=? "
                    "AND source_id=? AND source_revision=? AND extractor_version=?",
                    (run.bot_id, run.group_id, run.source_id, run.source_revision, "memory-facts-v1"),
                ).fetchone()
                if fact is None:
                    raise OperationError("memory_extraction_receipt_missing")
            if status == "complete":
                for domain in domains:
                    if domain == "fact":
                        continue
                    result = db.execute(
                        "SELECT 1 FROM domain_learning_results WHERE bot_id=? AND group_id=? "
                        "AND source_id=? AND source_revision=? AND domain=? "
                        "AND extractor_version=? UNION ALL SELECT 1 FROM domain_learning_failures "
                        "WHERE bot_id=? AND group_id=? AND source_id=? AND source_revision=? "
                        "AND domain=? AND extractor_version=? LIMIT 1",
                        (
                            run.bot_id,
                            run.group_id,
                            run.source_id,
                            run.source_revision,
                            domain,
                            "memory-domains-v1",
                            run.bot_id,
                            run.group_id,
                            run.source_id,
                            run.source_revision,
                            domain,
                            "memory-domains-v1",
                        ),
                    ).fetchone()
                    if result is None:
                        raise OperationError("memory_extraction_receipt_missing")
            cursor = db.execute(
                "UPDATE memory_extraction_runs SET status=?,error_code=?,updated_at=?,finished_at=? "
                "WHERE bot_id=? AND group_id=? AND source_id=? AND source_revision=? "
                "AND run_id=? AND (status='running' OR (status='complete' AND ?='cancelled'))",
                (
                    status,
                    error_code,
                    now,
                    now,
                    run.bot_id,
                    run.group_id,
                    run.source_id,
                    run.source_revision,
                    run.run_id,
                    status,
                ),
            )
            if cursor.rowcount != 1:
                raise OperationError("memory_extraction_run_changed")
            row = db.execute(
                "SELECT * FROM memory_extraction_runs WHERE bot_id=? AND group_id=? AND source_id=?",
                (run.bot_id, run.group_id, run.source_id),
            ).fetchone()
            if row is None:
                raise OperationError("invalid_memory_extraction_run")
            return _memory_extraction_run_result(row)

        return await self.transaction(finish)

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        owner = self.path.with_suffix(self.path.suffix + ".lock").open("ab")
        try:
            owner.seek(0)
            portalocker.lock(owner, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.AlreadyLocked as exc:
            owner.close()
            raise OperationError("executor_owned") from exc
        except BaseException as exc:
            owner.close()
            if isinstance(exc, portalocker.LockException):
                self.storage_errors += 1
                raise OperationError("executor_lock_unavailable") from exc
            raise
        self._owner = owner

        def connect() -> sqlite3.Connection:
            connection = sqlite3.connect(self.path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            return connection

        connection_task = asyncio.create_task(asyncio.to_thread(connect))
        try:
            self._connection = await drain_on_cancel(connection_task)
        except BaseException:
            if (
                connection_task.done()
                and not connection_task.cancelled()
                and connection_task.exception() is None
            ):
                self._connection = connection_task.result()
            await self.close()
            raise

        def initialize(db: sqlite3.Connection) -> None:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            existing = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            accepted_versions = {
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8,
                9,
                10,
                11,
                12,
                13,
                14,
                15,
                16,
                17,
                18,
                19,
                20,
                21,
                22,
                23,
                24,
                25,
                26,
                27,
                28,
                29,
                30,
                31,
                32,
                33,
                34,
                35,
                36,
                37,
                38,
                39,
            40,
            41,
            42,
            43,
            44,
            45,
            46,
            47,
            48,
            _SCHEMA_VERSION,
            }
            if version not in accepted_versions and (version != 0 or existing):
                raise OperationError("unsupported_schema")
            if version > _SCHEMA_VERSION:
                raise OperationError("unsupported_schema")
            if version == 0:
                db.executescript("""
                PRAGMA user_version=1;
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1),
                    revision INTEGER NOT NULL, grants TEXT NOT NULL);
                INSERT OR IGNORE INTO policy VALUES (1,0,'[]');
                CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL, state TEXT NOT NULL, code TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                    digest TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL,
                    action TEXT NOT NULL, code TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL, bot_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    receipt TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL DEFAULT (unixepoch()));
                CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                    identity TEXT NOT NULL, revision INTEGER NOT NULL, code TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL DEFAULT (unixepoch()));
            """)
                version = 1
            if version in {1, 2, 3, 4, 5}:
                db.execute("BEGIN IMMEDIATE")
                if version in {0, 1}:
                    db.execute(
                        "CREATE TABLE instance (id INTEGER PRIMARY KEY CHECK(id=1), "
                        "instance_id TEXT NOT NULL, bot_id TEXT NOT NULL)"
                    )
                    db.execute(
                        "CREATE TABLE deliveries (request_id TEXT PRIMARY KEY, total INTEGER NOT NULL "
                        "CHECK(total BETWEEN 1 AND 8))"
                    )
                if version in {0, 1, 2}:
                    db.execute(
                        "CREATE TABLE config_versions (revision INTEGER PRIMARY KEY "
                        "CHECK(revision >= 1), document TEXT NOT NULL)"
                    )
                if version in {1, 2, 3}:
                    db.execute(
                        "CREATE TABLE request_sources (owner_request_id TEXT NOT NULL, "
                        "source_request_id TEXT NOT NULL UNIQUE, "
                        "source_order INTEGER NOT NULL CHECK(source_order >= 0), "
                        "PRIMARY KEY(owner_request_id,source_request_id), "
                        "UNIQUE(owner_request_id,source_order))"
                    )
                    db.execute(
                        "INSERT INTO request_sources(owner_request_id,source_request_id,source_order) "
                        "SELECT id,id,0 FROM requests"
                    )
                _create_climate_schema(db)
                _create_archive_schema(db)
                _create_memory_schema(db)
                _create_story_schema(db)
                _create_schedule_schema(db)
                _create_dream_schema(db)
                _backfill_memory_revocations(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version == 6:
                db.execute("BEGIN IMMEDIATE")
                _create_archive_schema(db)
                _create_memory_schema(db)
                _create_story_schema(db)
                _create_schedule_schema(db)
                _create_dream_schema(db)
                _backfill_memory_revocations(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version == 7:
                db.execute("BEGIN IMMEDIATE")
                _create_memory_schema(db)
                _create_story_schema(db)
                _create_schedule_schema(db)
                _create_dream_schema(db)
                _backfill_memory_revocations(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version == 8:
                db.execute("BEGIN IMMEDIATE")
                _create_memory_schema(db)
                _create_story_schema(db)
                _create_schedule_schema(db)
                _create_dream_schema(db)
                _backfill_memory_revocations(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version == 9:
                db.execute("BEGIN IMMEDIATE")
                _create_story_schema(db)
                _create_schedule_schema(db)
                _create_dream_schema(db)
                _create_memory_extraction_runs_schema(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version == 10:
                db.execute("BEGIN IMMEDIATE")
                _create_story_schema(db)
                _create_schedule_schema(db)
                _create_dream_schema(db)
                _create_memory_extraction_runs_schema(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version == 11:
                db.execute("BEGIN IMMEDIATE")
                _create_schedule_schema(db)
                # Schema 11 owns the StoryArc tables but predates the
                # storylet projection.  Keep the schedule and schema-13
                # Story-only patch in one transaction so a failed upgrade
                # leaves the legacy database at schema 11 for retry.
                _create_story_schema(db)
                _create_dream_schema(db)
                _create_memory_extraction_runs_schema(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version == 12:
                db.execute("BEGIN IMMEDIATE")
                _create_story_schema(db)
                _create_dream_schema(db)
                _create_memory_extraction_runs_schema(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version == 13:
                db.execute("BEGIN IMMEDIATE")
                _create_story_schema(db)
                _create_dream_schema(db)
                _create_memory_extraction_runs_schema(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version == 14:
                db.execute("BEGIN IMMEDIATE")
                _create_story_schema(db)
                _create_memory_extraction_runs_schema(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            elif version == 15:
                db.execute("BEGIN IMMEDIATE")
                _create_memory_extraction_runs_schema(db)
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            else:
                has_settings = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='config_versions'"
                ).fetchone()
                has_sources = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='request_sources'"
                ).fetchone()
                has_climate_state = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='climate_states'"
                ).fetchone()
                has_climate_events = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='climate_events'"
                ).fetchone()
                has_archive_sources = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_sources'"
                ).fetchone()
                archive_source_columns = {
                    str(row[1]) for row in db.execute("PRAGMA table_info(archive_sources)")
                }
                has_archive_cursors = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_cursors'"
                ).fetchone()
                has_archive_runs = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_scan_runs'"
                ).fetchone()
                archive_run_columns = {
                    str(row[1]) for row in db.execute("PRAGMA table_info(archive_scan_runs)")
                }
                has_archive_tombstones = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='archive_source_tombstones'"
                ).fetchone()
                has_memory_candidates = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_candidates'"
                ).fetchone()
                has_memory_facts = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_facts'"
                ).fetchone()
                memory_candidate_columns = {
                    str(row[1]) for row in db.execute("PRAGMA table_info(memory_candidates)")
                }
                memory_fact_columns = {
                    str(row[1]) for row in db.execute("PRAGMA table_info(memory_facts)")
                }
                memory_fact_valid_from = next(
                    (
                        row for row in db.execute("PRAGMA table_info(memory_facts)")
                        if str(row[1]) == "valid_from"
                    ),
                    None,
                )
                has_memory_observations = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_observations'"
                ).fetchone()
                has_memory_events = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_events'"
                ).fetchone()
                has_memory_revocations = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_revocations'"
                ).fetchone()
                has_domain_learning_results = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='domain_learning_results'"
                ).fetchone()
                has_domain_learning_failures = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='domain_learning_failures'"
                ).fetchone()
                has_domain_learning_candidates = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='domain_learning_candidates'"
                ).fetchone()
                has_domain_learning_slang = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='domain_learning_slang_terms'"
                ).fetchone()
                has_domain_learning_slang_keys = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='domain_learning_slang_keys'"
                ).fetchone()
                has_domain_learning_slang_stoplist = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='domain_learning_slang_stoplist'"
                ).fetchone()
                has_domain_learning_style = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='domain_learning_style_items'"
                ).fetchone()
                has_domain_learning_episodes = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='domain_learning_episodes'"
                ).fetchone()
                has_memory_fact_extraction_results = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='memory_fact_extraction_results'"
                ).fetchone()
                has_memory_extraction_runs = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='memory_extraction_runs'"
                ).fetchone()
                has_social_experiences = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='social_experiences'"
                ).fetchone()
                social_experience_columns = {
                    str(row[1]) for row in db.execute("PRAGMA table_info(social_experiences)")
                }
                memory_extraction_run_columns = {
                    str(row[1]) for row in db.execute("PRAGMA table_info(memory_extraction_runs)")
                }
                memory_fact_extraction_columns = {
                    str(row[1])
                    for row in db.execute(
                        "PRAGMA table_info(memory_fact_extraction_results)"
                    )
                }
                has_story_arcs = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_arcs'"
                ).fetchone()
                has_story_arc_groups = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_arc_groups'"
                ).fetchone()
                has_story_events = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_events'"
                ).fetchone()
                has_story_social_links = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='story_social_experience_links'"
                ).fetchone()
                story_social_link_columns = {
                    str(row[1])
                    for row in db.execute("PRAGMA table_info(story_social_experience_links)")
                }
                story_social_link_foreign_keys = {
                    (str(row[2]), str(row[3]), str(row[4]))
                    for row in db.execute("PRAGMA foreign_key_list(story_social_experience_links)")
                }
                story_event_columns = {
                    str(row[1]) for row in db.execute("PRAGMA table_info(story_events)")
                }
                story_event_column_info = {
                    str(row[1]): row for row in db.execute("PRAGMA table_info(story_events)")
                }
                story_event_commit_seq_info = story_event_column_info.get("commit_seq")
                story_event_sql_row = db.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='story_events'"
                ).fetchone()
                story_event_sql = (
                    ""
                    if story_event_sql_row is None
                    else "".join(str(story_event_sql_row[0]).lower().split())
                )
                story_event_seq_indexes = tuple(
                    row
                    for row in db.execute("PRAGMA index_list(story_events)")
                    if str(row[1]) == "story_events_commit_seq"
                )
                story_event_seq_index_columns = tuple(
                    str(row[2])
                    for row in db.execute("PRAGMA index_info(story_events_commit_seq)")
                )
                story_event_commit_seq_valid = (
                    story_event_commit_seq_info is not None
                    and str(story_event_commit_seq_info[2]).upper() == "INTEGER"
                    and int(story_event_commit_seq_info[3]) == 1
                    and story_event_commit_seq_info[4] is None
                    and "check(commit_seq>0)" in story_event_sql
                    and len(story_event_seq_indexes) == 1
                    and int(story_event_seq_indexes[0][2]) == 1
                    and int(story_event_seq_indexes[0][4]) == 0
                    and story_event_seq_index_columns == ("commit_seq",)
                )
                has_story_outbox = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_projection_outbox'"
                ).fetchone()
                has_story_receipts = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_projection_receipts'"
                ).fetchone()
                has_story_life_states = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_life_states'"
                ).fetchone()
                has_story_partner_states = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_partner_states'"
                ).fetchone()
                has_storylet_states = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='storylet_states'"
                ).fetchone()
                storylet_state_columns = {
                    str(row[1]) for row in db.execute("PRAGMA table_info(storylet_states)")
                }
                storylet_state_foreign_keys = {
                    (str(row[2]), str(row[3]), str(row[4]))
                    for row in db.execute("PRAGMA foreign_key_list(storylet_states)")
                }
                has_dream_proposals = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dream_proposals'"
                ).fetchone()
                dream_proposal_columns = {
                    str(row[1]) for row in db.execute("PRAGMA table_info(dream_proposals)")
                }
                dream_proposal_foreign_keys = {
                    (str(row[2]), str(row[3]), str(row[4]))
                    for row in db.execute("PRAGMA foreign_key_list(dream_proposals)")
                }
                has_schedule_clocks = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_clocks'"
                ).fetchone()
                has_schedule_days = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_days'"
                ).fetchone()
                schedule_day_columns = {
                    str(row[1]) for row in db.execute("PRAGMA table_info(schedule_days)")
                }
                has_storylet_scan_cursors = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='storylet_scan_cursors'"
                ).fetchone()
                if (
                    has_settings is None
                    or has_sources is None
                    or has_climate_state is None
                    or has_climate_events is None
                    or has_archive_sources is None
                    or (version >= 24 and "text_expires_at" not in archive_source_columns)
                    or has_archive_cursors is None
                    or has_archive_runs is None
                    or (version >= 22 and "run_kind" not in archive_run_columns)
                    or has_archive_tombstones is None
                    or has_memory_candidates is None
                    or has_memory_facts is None
                    or not {"observed_at", "valid_from", "valid_to"}.issubset(
                        memory_candidate_columns
                    )
                    or (
                        version >= 17
                        and "suggestion_reason" not in memory_candidate_columns
                    )
                    or not {
                        "observed_at", "valid_from", "valid_to", "applied_at",
                        "suppressed_at", "suppression_candidate_id",
                    }.issubset(memory_fact_columns)
                    or (
                        version >= 20
                        and "supersedes_fact_id" not in memory_fact_columns
                    )
                    or memory_fact_valid_from is None
                    or int(memory_fact_valid_from[3]) != 0
                    or has_memory_observations is None
                    or has_memory_events is None
                    or has_memory_revocations is None
                    or has_story_arcs is None
                    or has_story_arc_groups is None
                    or has_story_events is None
                    or "origin_kind" not in story_event_columns
                    or not story_event_commit_seq_valid
                    or (
                        version >= 27
                        and (
                            "social_fiction" not in story_event_sql
                            or "social_experience" not in story_event_sql
                            or has_story_social_links is None
                            or not {
                                "bot_id",
                                "group_id",
                                "experience_id",
                                "event_id",
                                "arc_id",
                                "created_at",
                            }.issubset(story_social_link_columns)
                            or ("story_events", "bot_id", "bot_id")
                            not in story_social_link_foreign_keys
                            or ("story_events", "event_id", "event_id")
                            not in story_social_link_foreign_keys
                            or ("social_experiences", "experience_id", "experience_id")
                            not in story_social_link_foreign_keys
                        )
                    )
                    or has_story_outbox is None
                    or has_story_receipts is None
                    or has_story_life_states is None
                    or has_story_partner_states is None
                    or has_storylet_states is None
                    or not {
                        "bot_id",
                        "group_id",
                        "arc_id",
                        "registry_fingerprint",
                        "state_json",
                        "revision",
                        "updated_at",
                    }.issubset(storylet_state_columns)
                    or ("story_arcs", "bot_id", "bot_id") not in storylet_state_foreign_keys
                    or ("story_arcs", "arc_id", "arc_id") not in storylet_state_foreign_keys
                    or has_dream_proposals is None
                    or not {
                        "bot_id",
                        "group_id",
                        "proposal_id",
                        "target_arc_id",
                        "target_arc_revision",
                        "source_fingerprint",
                        "created_at",
                        "proposal_json",
                        "proposal_digest",
                        "decision_status",
                        "decision_digest",
                        "decided_at",
                        "reason",
                        "committed_event_id",
                        "committed_at",
                    }.issubset(dream_proposal_columns)
                    or ("story_arcs", "bot_id", "bot_id") not in dream_proposal_foreign_keys
                    or ("story_arcs", "target_arc_id", "arc_id")
                    not in dream_proposal_foreign_keys
                    or ("story_events", "bot_id", "bot_id") not in dream_proposal_foreign_keys
                    or ("story_events", "committed_event_id", "event_id")
                    not in dream_proposal_foreign_keys
                    or has_schedule_clocks is None
                    or has_schedule_days is None
                    or (
                        version >= 18
                        and not {"storylet_fingerprint", "arc_revisions"}.issubset(
                            schedule_day_columns
                        )
                    )
                    or (version >= 18 and has_storylet_scan_cursors is None)
                    or (
                        version >= 19
                        and (
                            has_domain_learning_results is None
                            or has_domain_learning_candidates is None
                            or has_domain_learning_slang is None
                            or has_domain_learning_slang_keys is None
                            or has_domain_learning_slang_stoplist is None
                            or has_domain_learning_style is None
                            or has_domain_learning_episodes is None
                        )
                    )
                    or (
                        version >= 21
                        and (
                            has_memory_fact_extraction_results is None
                            or not {
                                "result_id",
                                "bot_id",
                                "group_id",
                                "source_id",
                                "domain",
                                "extractor_version",
                                "source_revision",
                                "result_status",
                                "candidate_ids_json",
                                "result_digest",
                                "created_at",
                            }.issubset(memory_fact_extraction_columns)
                        )
                    )
                    or (version >= 23 and has_domain_learning_failures is None)
                    or (
                        version >= 25
                        and (
                            has_memory_extraction_runs is None
                            or not {
                                "run_id",
                                "bot_id",
                                "group_id",
                                "source_id",
                                "source_revision",
                                "status",
                                "stage",
                                "error_code",
                                "started_at",
                                "updated_at",
                                "finished_at",
                            }.issubset(memory_extraction_run_columns)
                        )
                    )
                    or (
                        version >= 26
                        and (
                            has_social_experiences is None
                            or not {
                                "experience_id",
                                "episode_id",
                                "source_id",
                                "source_revision",
                                "bot_id",
                                "group_id",
                                "user_id",
                                "platform_message_id",
                                "observed_at",
                                "reply_action_id",
                                "receipt",
                                "status",
                            }.issubset(social_experience_columns)
                        )
                    )
                ):
                    raise OperationError("unsupported_schema")
            if version < 16:
                _upgrade_memory_schema_16(db)
            if version < 17:
                _upgrade_memory_schema_17(db)
            if version < 18:
                _upgrade_storylet_schema_18(db)
            if version < 19:
                _upgrade_domain_learning_schema_19(db)
            if version < 20:
                _upgrade_temporal_trace_schema_20(db)
            if version < 21:
                _upgrade_memory_fact_extraction_schema_21(db)
            if version < 22:
                _upgrade_archive_scan_runs_schema_22(db)
            if version < 23:
                _upgrade_domain_learning_failures_schema_23(db)
            if version < 24:
                _upgrade_archive_text_deadline_schema_24(db)
            if version < 25:
                _upgrade_memory_extraction_runs_schema_25(db)
            if version < 26:
                _upgrade_social_experiences_schema_26(db)
            if version < 27:
                _upgrade_social_story_schema_27(db)
            if version < 28:
                _upgrade_style_management_schema_28(db)
            elif any(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                (table,)).fetchone() is None for table in
                     ("style_scope_versions", "style_feedback", "style_profiles")):
                raise OperationError("unsupported_schema")
            if version < 29:
                _upgrade_domain_observations_schema_29(db)
            if (
                not {"pool_key", "source_revision", "subject_id", "candidate_revision", "object_revision"}
                <= {str(row[1]) for row in db.execute("PRAGMA table_info(domain_learning_observations)")}
                or not {"sources_json", "threshold", "revision", "status",
                        "candidate_revision", "object_revision"}
                <= {str(row[1]) for row in db.execute("PRAGMA table_info(domain_learning_observation_jobs)")}
            ):
                raise OperationError("unsupported_schema")
            if version < 30:
                _upgrade_knowledge_schema_30(db)
            required_knowledge_columns = {
                "knowledge_sources": {
                    "bot_id", "group_id", "source_id", "uploader_id", "source_label", "title", "format",
                    "classification", "body", "body_bytes", "content_hash", "revision", "content_revision",
                    "review_status", "reviewed_content_revision", "review_actor", "status", "apply_actor",
                    "upload_policy_revision", "review_policy_revision", "apply_policy_revision",
                    "index_version", "created_at", "updated_at",
                },
                "knowledge_chunks": {
                    "bot_id", "group_id", "chunk_id", "source_id", "source_revision", "ordinal", "start_char",
                    "end_char", "start_line", "end_line", "title", "body", "token_count",
                },
                "knowledge_chunk_terms": {"bot_id", "group_id", "term", "chunk_id", "frequency"},
            }
            if any(
                not required <= {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
                for table, required in required_knowledge_columns.items()
            ):
                raise OperationError("unsupported_schema")
            if version < 31:
                _upgrade_character_identity_schema_31(db)
            required_character_columns = {
                "character_identities": {
                    "identity_id", "bot_id", "group_id", "teacher_id", "image_sha256", "label",
                    "revision", "status", "created_at", "updated_at",
                },
                "character_identity_sources": {
                    "identity_id", "identity_revision", "source_id", "source_revision",
                    "origin_event_id", "platform_message_id", "turn_id", "segment_index", "source_kind",
                },
                "character_identity_receipts": {
                    "receipt_id", "operation_id", "identity_id", "identity_revision", "request_digest",
                    "action", "status", "audit_id", "policy_revision", "created_at",
                },
            }
            if any(
                not required <= {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
                for table, required in required_character_columns.items()
            ):
                raise OperationError("unsupported_schema")
            if version < 32:
                _upgrade_graph_schema_32(db)
            required_graph_columns = {
                "graph_entities": {"bot_id", "group_id", "entity_id", "kind"},
                "graph_relations": {"bot_id", "group_id", "object_id", "revision", "subject_id",
                                    "predicate", "target_id", "pointer_json", "payload_digest",
                                    "review_status", "status", "review_actor", "apply_actor",
                                    "policy_revision", "created_at", "updated_at"},
                "graph_aliases": {"bot_id", "group_id", "object_id", "revision", "entity_id",
                                  "alias_surface", "alias_norm", "pointer_json", "payload_digest",
                                  "review_status", "status", "review_actor", "apply_actor",
                                  "policy_revision", "created_at", "updated_at"},
            }
            if any(
                not required <= {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
                for table, required in required_graph_columns.items()
            ):
                raise OperationError("unsupported_schema")
            if version < 33:
                _upgrade_memory_cards_schema_33(db)
            required_card_columns = {
                "bot_id", "group_id", "fact_id", "category", "classification_revision",
                "actor", "policy_revision", "updated_at",
            }
            if not required_card_columns <= {
                str(row[1]) for row in db.execute("PRAGMA table_info(memory_card_classifications)")
            }:
                raise OperationError("unsupported_schema")
            if version < 34:
                _upgrade_affection_schema_34(db)
            required_affection = {
                "affection_relations": {"bot_id", "group_id", "subject_id", "revision"},
                "affection_contributions": {"bot_id", "group_id", "subject_id", "event_id",
                    "source_id", "source_revision", "source_digest", "action_id", "receipt_id", "day",
                    "revision", "actor", "policy_revision", "created_at"},
            }
            if any(not columns <= {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
                   for table, columns in required_affection.items()):
                raise OperationError("unsupported_schema")
            if version < 35:
                _upgrade_food_schema_35(db)
            required_food = {
                "food_preferences": {"bot_id", "group_id", "user_id", "revision", "likes",
                                     "dislikes", "location"},
                "food_served": {"bot_id", "group_id", "user_id", "event_id", "name",
                                "action_id", "receipt_id", "served_at"},
                "food_tutorial_claims": {"claim_key", "user_id", "claimed_at"},
            }
            if any(not columns <= {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
                   for table, columns in required_food.items()):
                raise OperationError("unsupported_schema")
            if version < 36:
                _upgrade_action_scope_schema_36(db)
            if not {"scope_kind", "private_user_id"} <= {
                str(row[1]) for row in db.execute("PRAGMA table_info(actions)")
            }:
                raise OperationError("unsupported_schema")
            if version < 37:
                _upgrade_visibility_policy_schema_37(db)
            if "visibility_grants" not in {
                str(row[1]) for row in db.execute("PRAGMA table_info(policy)")
            }:
                raise OperationError("unsupported_schema")
            if version < 38:
                _upgrade_sticker_schema_38(db)
            required_stickers = {
                "sticker_catalog_head": {"id", "revision"},
                "sticker_assets": {"sticker_id", "bot_id", "group_id", "content_hash", "mime_type",
                    "byte_size", "width", "height", "image_data", "description", "usage_hint",
                    "ocr_text", "intent_tags", "affect_tags", "status", "revision"},
                "sticker_operations": {"operation_id", "bot_id", "group_id", "actor", "operation",
                    "digest", "catalog_revision", "sticker_id", "entry_revision", "status",
                    "policy_revision", "created_at"},
            }
            if any(not columns <= {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
                    for table, columns in required_stickers.items()):
                raise OperationError("unsupported_schema")
            if version < 39:
                _upgrade_journal_schema_39(db)
            required_journal = {
                "journal_drafts": {"draft_id", "bot_id", "group_id", "root_id", "revision",
                    "supersedes_draft_id", "source_event_id", "source_hash", "body", "body_hash",
                    "content_hash", "state", "created_at"},
                "journal_reviews": {"draft_id", "actor", "decision", "body_hash", "source_hash",
                    "content_hash", "approval_scope", "created_at"},
                "journal_operations": {"operation_id", "bot_id", "group_id", "actor", "operation",
                    "digest", "result_draft_id", "policy_revision", "created_at"},
            }
            if any(not columns <= {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
                    for table, columns in required_journal.items()):
                raise OperationError("unsupported_schema")
            if version < 40:
                _upgrade_self_fact_graph_schema_40(db)
            if version < 41:
                _upgrade_episode_schema_41(db)
            if version < 42:
                _upgrade_affection_adjustments_schema_42(db)
            if version < 43:
                _upgrade_food_private_schema_43(db)
            if version < 44:
                _upgrade_research_journal_schema_44(db)
            if version < 45:
                _upgrade_qq_delivery_schema_45(db)
            if version < 46:
                _upgrade_climate_generation_schema_46(db)
            if "generation" not in {
                str(row[1]) for row in db.execute("PRAGMA table_info(climate_states)")
            }:
                raise OperationError("unsupported_schema")
            if version < 47:
                _upgrade_memory_matters_schema_47(db)
            required_matters = {
                "matter_id", "bot_id", "group_id", "subject_id", "source_id", "source_revision",
                "summary", "condition_text", "state", "revision", "observed_at", "due_at",
                "expires_at", "replaces_matter_id", "reason", "actor", "created_at", "updated_at",
            }
            if not required_matters <= {
                str(row[1]) for row in db.execute("PRAGMA table_info(memory_matters)")
            }:
                raise OperationError("unsupported_schema")
            if version < 48:
                _upgrade_climate_sources_schema_48(db)
            required_climate_sources = {
                "bot_id", "group_id", "user_id", "generation", "source_id", "kind",
                "origin_source_id", "owner_id", "owner_revision", "author_id",
                "retained_at", "expires_at", "event_id", "receipt_id",
            }
            if (not required_climate_sources <= {
                    str(row[1]) for row in db.execute("PRAGMA table_info(climate_sources)")
                } or "source_id" not in {
                    str(row[1]) for row in db.execute("PRAGMA table_info(climate_events)")
                }):
                raise OperationError("unsupported_schema")
            if version < 49:
                _upgrade_contact_schema_49(db)
            if ("contact_consents" not in {
                    str(row[1]) for row in db.execute("PRAGMA table_info(policy)")
                } or "origin_kind" not in {
                    str(row[1]) for row in db.execute("PRAGMA table_info(requests)")
                } or not {"request_id", "authority_revision", "user_generation", "group_generation",
                          "decision_started_at", "send_started_at", "active", "revision"} <= {
                    str(row[1]) for row in db.execute("PRAGMA table_info(contact_requests)")
                }):
                raise OperationError("unsupported_schema")
            required_qq = {
                "actions": set(_QQ_ACTION_COLUMNS),
                "qq_delivery_state": {"account_id", "scope_key", "held", "revision", "reason",
                    "occurred_at", "last_settled_at", "action_id", "audit_id"},
                "qq_delivery_metadata": {"id", "database_id"},
            }
            if any(not columns <= {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
                   for table, columns in required_qq.items()):
                raise OperationError("unsupported_schema")
            identity_row = db.execute("SELECT database_id FROM qq_delivery_metadata WHERE id=1").fetchone()
            if identity_row is None:
                raise OperationError("unsupported_schema")
            if "content_kind" not in {str(row[1]) for row in db.execute("PRAGMA table_info(journal_drafts)")}:
                raise OperationError("unsupported_schema")
            for table in ("journal_public_consents", "journal_public_sources", "journal_compositions",
                          "journal_deliveries", "research_message_events"):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                              (table,)).fetchone() is None:
                    raise OperationError("unsupported_schema")
            required_food_private = {
                "food_private_preferences": {"bot_id", "private_user_id", "user_id", "revision",
                    "likes", "dislikes", "location"},
                "food_private_served": {"bot_id", "private_user_id", "user_id", "event_id",
                    "name", "action_id", "receipt_id", "served_at"},
            }
            if any(not columns <= {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
                   for table, columns in required_food_private.items()):
                raise OperationError("unsupported_schema")
            if not {"bot_id", "group_id", "subject_id", "revision", "offset", "requested_score",
                    "actor", "operation_id", "digest", "policy_revision", "updated_at"} <= {
                str(row[1]) for row in db.execute("PRAGMA table_info(affection_adjustments)")
            }:
                raise OperationError("unsupported_schema")
            if not {"decay_at", "last_used_at"} <= {
                str(row[1]) for row in db.execute("PRAGMA table_info(domain_learning_episodes)")
            }:
                raise OperationError("unsupported_schema")
            if self.instance_id is not None and self.bot_id is not None:
                identity = db.execute("SELECT instance_id,bot_id FROM instance WHERE id=1").fetchone()
                if identity is not None and tuple(identity) != (self.instance_id, self.bot_id):
                    raise OperationError("instance_mismatch")
                if identity is None:
                    historical = {row[0] for row in db.execute("SELECT DISTINCT bot_id FROM actions")}
                    try:
                        grants = json.loads(db.execute("SELECT grants FROM policy WHERE id=1").fetchone()[0])
                        historical.update(grant["scope"]["bot_id"] for grant in grants)
                    except (ValueError, TypeError, KeyError) as exc:
                        raise OperationError("invalid_policy") from exc
                    if historical - {self.bot_id}:
                        raise OperationError("instance_mismatch")
                    db.execute("INSERT INTO instance VALUES (1,?,?)", (self.instance_id, self.bot_id))
            db.execute("CREATE INDEX IF NOT EXISTS audit_identity_kind ON audit(identity,kind,id)")
            db.execute("CREATE INDEX IF NOT EXISTS actions_request ON actions(request_id)")
            opened_at = time.time()
            # Recovery settles an interrupted write as unknown now. Its debt
            # must not disappear merely because the original commit was old.
            marks = ",".join("?" for _ in _QQ_LEGACY_WRITES)
            db.execute(
                "UPDATE actions SET qq_budget_anchor=MAX(COALESCE(qq_budget_anchor,created_at),?),"
                "qq_settled_at=? WHERE state='dispatching' AND "
                f"(qq_account_id IS NOT NULL OR action IN ({marks}))",
                (opened_at, opened_at, *_QQ_LEGACY_WRITES),
            )
            db.execute("UPDATE actions SET state='unknown' WHERE state='dispatching'")
            db.execute("UPDATE actions SET state='cancelled_before_dispatch' WHERE state='queued'")
            db.execute(
                "UPDATE requests SET state='unknown' WHERE id IN "
                "(SELECT request_id FROM actions WHERE state='unknown')"
            )
            db.execute(
                "UPDATE requests SET state='cancelled_before_dispatch' "
                "WHERE state IN ('accepted','running','queued')"
            )
            # Recovery closes the request owner above; release its contact slot
            # without erasing the seen cause or refunding attempted usage.
            db.execute(
                "UPDATE contact_requests SET active=0,revision=revision+1 "
                "WHERE active=1 AND request_id IN (SELECT id FROM requests "
                "WHERE origin_kind='bot_contact' AND state IN "
                "('succeeded','failed','cancelled_before_dispatch','unknown'))"
            )
            # The exclusive Store lock is held for this connection's lifetime,
            # so any archive scan still marked running belongs to a prior
            # Store owner and must be resumed explicitly from its checkpoint.
            qq_accounts = {str(row[0]) for row in db.execute("SELECT account_id FROM qq_delivery_state")}
            qq_accounts.update(str(row[0]) for row in db.execute(
                "SELECT DISTINCT qq_account_id FROM actions WHERE qq_account_id IS NOT NULL",
            ))
            for account_id in qq_accounts:
                _qq_recover_account(db, account_id, opened_at)
            db.execute(
                "UPDATE archive_scan_runs SET status='abandoned',finished_at=? "
                "WHERE status='running'",
                (opened_at,),
            )
            db.execute(
                "UPDATE archive_cursors SET status='abandoned',updated_at=? "
                "WHERE status='running'",
                (opened_at,),
            )
            db.execute(
                "UPDATE memory_extraction_runs SET status='abandoned',updated_at=?,"
                "finished_at=? WHERE status='running'",
                (opened_at, opened_at),
            )

        try:
            await self.transaction(initialize)
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        async def release() -> None:
            async with self._lock:
                if self._connection is not None:
                    await asyncio.to_thread(self._connection.close)
                    self._connection = None
                if self._owner is not None:
                    self._owner.close()
                    self._owner = None

        await drain_on_cancel(asyncio.create_task(release()))

    @staticmethod
    def read_saved_settings(path: Path | str) -> dict[str, JsonValue] | None:
        """Read the newest saved settings document without creating or changing a database."""
        resolved = Path(path).resolve()
        if not resolved.exists():
            return None
        uri = resolved.as_uri() + "?mode=ro"
        database: sqlite3.Connection | None = None
        try:
            database = sqlite3.connect(uri, uri=True)
            database.row_factory = sqlite3.Row
            version = database.execute("PRAGMA user_version").fetchone()[0]
            if type(version) is not int or version < 0 or version > _SCHEMA_VERSION:
                raise OperationError("unsupported_schema")
            existing = database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version == 0 and existing:
                raise OperationError("unsupported_schema")
            has_settings = database.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='config_versions'"
            ).fetchone()
            if has_settings is None:
                return None
            row = database.execute(
                "SELECT revision,document FROM config_versions ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            return None if row is None else _settings_result(row)[1]
        except OperationError:
            raise
        except sqlite3.Error as exc:
            if not resolved.exists():
                return None
            raise OperationError("storage_unavailable") from exc
        finally:
            if database is not None:
                database.close()

    @staticmethod
    def _climate_record_transaction(
        db: sqlite3.Connection, key: tuple[str, str, str]
    ) -> ClimateStateRecord | None:
        _validate_climate_key(key)
        row = db.execute(
            "SELECT * FROM climate_states WHERE bot_id=? AND group_id=? AND user_id=?",
            key,
        ).fetchone()
        if row is None:
            return None
        values = tuple(_climate_float(row[dimension]) for dimension in _CLIMATE_DIMENSIONS)
        baselines = tuple(
            _climate_float(row[f"baseline_{dimension}"]) for dimension in _CLIMATE_DIMENSIONS
        )
        last_update = _climate_float(row["last_update"], bounded=False)
        revision = row["revision"]
        if type(revision) is not int or revision < 0:
            raise OperationError("invalid_climate_state")
        source_status = row["source_status"]
        if not isinstance(source_status, str) or source_status not in _CLIMATE_STATUSES:
            raise OperationError("invalid_climate_state")
        source_refs = _decode_climate_refs(row["source_refs"])
        event_rows = db.execute(
            "SELECT sensor,event_id FROM climate_events "
            "WHERE bot_id=? AND group_id=? AND user_id=? ORDER BY event_seq",
            key,
        ).fetchall()
        event_ids = tuple(
            (
                _climate_identity(event_row["sensor"], limit=64, code="invalid_climate_event"),
                _climate_identity(event_row["event_id"], limit=128, code="invalid_climate_event"),
            )
            for event_row in event_rows
        )
        if any(sensor not in _CLIMATE_SENSORS for sensor, _ in event_ids):
            raise OperationError("invalid_climate_event")
        return ClimateStateRecord(
            key=key,
            values=values,
            baselines=baselines,
            last_update=last_update,
            revision=revision,
            generation=str(row["generation"]),
            source_status=source_status,
            source_refs=source_refs,
            event_ids=event_ids,
            source_proofs=tuple(
                ClimateSourceProof(
                    key=key, kind=cast(Literal["message", "receipt", "schedule", "calendar", "clock"],
                                       source["kind"]),
                    source_id=str(source["source_id"]), origin_source_id=str(source["origin_source_id"]),
                    owner_id=str(source["owner_id"]), owner_revision=str(source["owner_revision"]),
                    author_id=str(source["author_id"]), retained_at=float(source["retained_at"]),
                    expires_at=float(source["expires_at"]), event_id=str(source["event_id"]),
                    receipt_id=str(source["receipt_id"]),
                ) for source in db.execute(
                    "SELECT * FROM climate_sources WHERE bot_id=? AND group_id=? AND user_id=? "
                    "ORDER BY rowid", key,
                )
            ),
        )

    @staticmethod
    def _climate_neutral_transaction(
        db: sqlite3.Connection, key: tuple[str, str, str],
    ) -> ClimateStateRecord:
        defaults = (.5, .5, .5, 0., .5, .5)
        columns = (*_CLIMATE_DIMENSIONS, *(f"baseline_{dimension}" for dimension in _CLIMATE_DIMENSIONS))
        updates = ",".join(f"{column}=excluded.{column}" for column in (
            *columns, "last_update", "revision", "source_status", "source_refs", "generation",
        ))
        db.execute(
            "INSERT INTO climate_states(bot_id,group_id,user_id," + ",".join(columns)
            + ",last_update,revision,source_status,source_refs,generation) VALUES (?,?,?,"
            + ",".join("?" for _ in columns)
            + ",0,0,'unavailable','[]',?) ON CONFLICT(bot_id,group_id,user_id) DO UPDATE SET " + updates,
            (*key, *defaults, *defaults, uuid.uuid4().hex),
        )
        record = Store._climate_record_transaction(db, key)
        assert record is not None
        return record

    @staticmethod
    def _climate_invalidation_audit_transaction(
        db: sqlite3.Connection, key: tuple[str, str, str], row: sqlite3.Row, *, actor: str, reason: str,
    ) -> None:
        db.execute(
            "INSERT INTO audit(kind,identity,revision,code,details) "
            "VALUES ('climate',?,?,'source_invalidated',?)",
            (actor, int(row["revision"]), json.dumps({
                "scope": key, "generation": row["generation"], "reason": reason,
            }, separators=(",", ":"))),
        )

    @staticmethod
    def climate_invalidate_transaction(
        db: sqlite3.Connection, key: tuple[str, str, str], *, actor: str, reason: str,
    ) -> ClimateStateRecord | None:
        """Clear all contributions and rotate the same bounded row's neutral epoch."""
        row = db.execute(
            "SELECT generation,revision FROM climate_states "
            "WHERE bot_id=? AND group_id=? AND user_id=?", key,
        ).fetchone()
        if row is None:
            return None
        db.execute("DELETE FROM climate_events WHERE bot_id=? AND group_id=? AND user_id=?", key)
        db.execute("DELETE FROM climate_sources WHERE bot_id=? AND group_id=? AND user_id=?", key)
        record = Store._climate_neutral_transaction(db, key)
        Store._climate_invalidation_audit_transaction(db, key, row, actor=actor, reason=reason)
        return record

    @staticmethod
    def _climate_trim_keys_transaction(
        db: sqlite3.Connection, key: tuple[str, str, str], max_keys: int,
    ) -> tuple[tuple[str, str, str], ...]:
        count = int(db.execute("SELECT count(*) FROM climate_states").fetchone()[0])
        victims = db.execute(
            "SELECT bot_id,group_id,user_id,generation,revision FROM climate_states "
            "WHERE NOT (bot_id=? AND group_id=? AND user_id=?) "
            "ORDER BY last_update,rowid LIMIT ?", (*key, max(0, count - max_keys)),
        ).fetchall()
        evicted_keys: list[tuple[str, str, str]] = []
        for row in victims:
            victim_key = (str(row["bot_id"]), str(row["group_id"]), str(row["user_id"]))
            evicted_keys.append(victim_key)
            db.execute("DELETE FROM climate_events WHERE bot_id=? AND group_id=? AND user_id=?", victim_key)
            db.execute("DELETE FROM climate_sources WHERE bot_id=? AND group_id=? AND user_id=?", victim_key)
            db.execute("DELETE FROM climate_states WHERE bot_id=? AND group_id=? AND user_id=?", victim_key)
            Store._climate_invalidation_audit_transaction(
                db, victim_key, row, actor="climate", reason="key_capacity",
            )
        return tuple(evicted_keys)

    @staticmethod
    def _climate_proof_time(proof_now: float | None) -> float:
        return _climate_float(time.time() if proof_now is None else proof_now, bounded=False)

    @staticmethod
    def _climate_source_validate(source: ClimateSourceProof, key: tuple[str, str, str]) -> None:
        if source.key != key:
            raise OperationError("invalid_climate_source")
        if source.kind not in _CLIMATE_SOURCE_SENSORS or source.author_id != key[2]:
            raise OperationError("invalid_climate_source")
        for value, limit in (
            (source.source_id, 128), (source.origin_source_id, 128), (source.owner_id, 512),
            (source.owner_revision, 128), (source.author_id, 64), (source.event_id, 128),
        ):
            _climate_identity(value, limit=limit, code="invalid_climate_source")
        canonical = "src_" + hashlib.sha256("\0".join((*key[:2], source.event_id)).encode()).hexdigest()
        if source.origin_source_id != canonical:
            raise OperationError("invalid_climate_source")
        retained = _climate_float(source.retained_at, bounded=False)
        expires = _climate_float(source.expires_at, bounded=False)
        if not retained < expires <= retained + 3600:
            raise OperationError("invalid_climate_source")
        if source.kind == "receipt":
            _climate_identity(source.receipt_id, limit=128, code="invalid_climate_source")
        elif source.receipt_id:
            raise OperationError("invalid_climate_source")

    @staticmethod
    def _climate_source_current_transaction(
        db: sqlite3.Connection, source: ClimateSourceProof, proof_now: float,
    ) -> bool:
        if not source.retained_at <= proof_now < source.expires_at:
            return False
        if db.execute(
            "SELECT 1 FROM archive_source_tombstones WHERE source_id=? AND bot_id=? AND group_id=?",
            (source.origin_source_id, *source.key[:2]),
        ).fetchone() is not None:
            return False
        if source.kind != "receipt":
            request = db.execute("SELECT digest FROM requests WHERE id=?", (source.event_id,)).fetchone()
            if request is not None:
                if source.kind == "message" and request["digest"] != source.owner_revision:
                    return False
                if db.execute(
                    "SELECT 1 FROM climate_sources WHERE bot_id=? AND group_id=? AND user_id=? "
                    "AND origin_source_id=? AND retained_at=?",
                    (*source.key, source.origin_source_id, source.retained_at),
                ).fetchone() is None:
                    return False
        if source.kind == "receipt":
            row = db.execute("SELECT * FROM actions WHERE id=?", (source.owner_id,)).fetchone()
            return row is not None and (
                row["state"] == "succeeded" and row["action"] in {"message.reply", "message.sticker"}
                and row["scope_kind"] == "group" and row["bot_id"] == source.key[0]
                and row["group_id"] == source.key[1] and row["subject"] == source.key[2]
                and row["digest"] == source.owner_revision and row["receipt"] == source.receipt_id
                and db.execute(
                    "SELECT 1 FROM request_sources WHERE owner_request_id=? AND source_request_id=?",
                    (row["request_id"], source.event_id),
                ).fetchone() is not None
            )
        if source.kind == "schedule":
            row = db.execute(
                "SELECT * FROM schedule_days WHERE bot_id=? AND group_id=? AND day_id=?",
                (*source.key[:2], source.owner_id),
            ).fetchone()
            return row is not None and row["input_digest"] == source.owner_revision and (
                datetime.fromtimestamp(proof_now, ZoneInfo(str(row["timezone"]))).date().isoformat()
                == row["local_day"]
            )
        # Calendar and clock have runtime owners. Their exact Config/fact proof is
        # checked at the Conversation commit and actual consumer, not invented here.
        return True

    @staticmethod
    def _climate_sources_current_transaction(
        db: sqlite3.Connection, record: ClimateStateRecord, proof_now: float | None,
    ) -> bool:
        as_of = Store._climate_proof_time(proof_now)
        if not record.source_proofs:
            defaults = (.5, .5, .5, 0., .5, .5)
            return (record.source_status == "unavailable" and record.revision == 0
                    and record.last_update == 0 and record.values == defaults and record.baselines == defaults
                    and not record.event_ids and not record.source_refs)
        for source in record.source_proofs:
            Store._climate_source_validate(source, record.key)
            if not Store._climate_source_current_transaction(db, source, as_of):
                return False
        return not db.execute(
            "SELECT 1 FROM climate_events e LEFT JOIN climate_sources s "
            "ON s.bot_id=e.bot_id AND s.group_id=e.group_id AND s.user_id=e.user_id "
            "AND s.source_id=e.source_id AND s.generation=? "
            "WHERE e.bot_id=? AND e.group_id=? AND e.user_id=? AND s.source_id IS NULL LIMIT 1",
            (record.generation, *record.key),
        ).fetchone()

    @staticmethod
    def climate_invalidate_source_transaction(
        db: sqlite3.Connection, *, source_id: str, bot_id: str, group_id: str,
        actor: str, reason: str,
    ) -> tuple[tuple[str, str, str], ...]:
        keys = tuple((str(row[0]), str(row[1]), str(row[2])) for row in db.execute(
            "SELECT DISTINCT bot_id,group_id,user_id FROM climate_sources "
            "WHERE bot_id=? AND group_id=? AND (origin_source_id=? OR source_id=?)",
            (bot_id, group_id, source_id, source_id),
        ))
        for key in keys:
            Store.climate_invalidate_transaction(db, key, actor=actor, reason=reason)
        return keys

    @staticmethod
    def _climate_batch(
        key: tuple[str, str, str], sources: Sequence[ClimateSourceProof], events: Sequence[ClimateEvent],
        max_sources_per_key: int, max_events_per_key: int,
    ) -> tuple[dict[str, ClimateSourceProof], tuple[tuple[str, str], ...]]:
        if not 1 <= max_sources_per_key <= 32 or not 1 <= max_events_per_key <= 256:
            raise OperationError("invalid_climate_limits")
        if not events or not sources:
            raise OperationError("invalid_climate_source")
        source_map: dict[str, ClimateSourceProof] = {}
        for source in sources:
            Store._climate_source_validate(source, key)
            if source.source_id in source_map and source_map[source.source_id] != source:
                raise OperationError("invalid_climate_source")
            source_map[source.source_id] = source
        identities: list[tuple[str, str]] = []
        used: set[str] = set()
        for event in events:
            _climate_identity(event.event_id, limit=128, code="invalid_climate_event")
            _climate_identity(event.source_ref, limit=128, code="invalid_climate_source")
            _climate_float(event.observed_at, bounded=False)
            if event.source is None or source_map.get(event.source.source_id) != event.source:
                raise OperationError("invalid_climate_source")
            if event.sensor not in _CLIMATE_SOURCE_SENSORS[event.source.kind]:
                raise OperationError("invalid_climate_source")
            identities.append((event.sensor, event.event_id))
            used.add(event.source.source_id)
        if (len(set(identities)) != len(identities) or used != source_map.keys()
                or len(source_map) > max_sources_per_key or len(events) > max_events_per_key):
            raise OperationError("invalid_climate_limits")
        return source_map, tuple(identities)

    async def climate_prepare(
        self, key: tuple[str, str, str], *, sources: Sequence[ClimateSourceProof],
        events: Sequence[ClimateEvent], expected_generation: str, expected_revision: int,
        authorize: Callable[[sqlite3.Connection], object] | None = None,
        proof_now: float | None = None, max_sources_per_key: int = 32,
        max_events_per_key: int = 256, max_keys: int = 4096,
    ) -> ClimateStateRecord:
        """Commit any required whole-key rollover before the engine computes new values."""
        _validate_climate_key(key)
        self._validate_climate_scope(key)
        _climate_identity(expected_generation, limit=128, code="stale_climate_source")
        source_map, identities = self._climate_batch(
            key, sources, events, max_sources_per_key, max_events_per_key,
        )
        if type(expected_revision) is not int or expected_revision < 0:
            raise OperationError("climate_revision_conflict")
        if type(max_keys) is not int or not 1 <= max_keys <= 4096:
            raise OperationError("invalid_climate_limits")

        def prepare(db: sqlite3.Connection) -> ClimateStateRecord:
            db.execute("BEGIN IMMEDIATE")
            if authorize is not None:
                authorize(db)
            current = self._climate_record_transaction(db, key)
            if (None if current is None else current.generation) != expected_generation:
                raise OperationError("stale_climate_source")
            if (0 if current is None else current.revision) != expected_revision:
                raise OperationError("climate_revision_conflict")
            as_of = self._climate_proof_time(proof_now)
            if any(not self._climate_source_current_transaction(db, source, as_of)
                   for source in source_map.values()):
                raise OperationError("stale_climate_source")
            assert current is not None
            if not self._climate_sources_current_transaction(db, current, as_of):
                neutral = self.climate_invalidate_transaction(
                    db, key, actor="climate", reason="source_expired",
                )
                assert neutral is not None
                return neutral
            existing = {source.source_id: source for source in current.source_proofs}
            new_ids = set(identities) - set(current.event_ids)
            new_sources = {event.source.source_id: event.source for event in events
                           if (event.sensor, event.event_id) in new_ids and event.source is not None}
            for source_id, source in source_map.items():
                if source_id in existing and existing[source_id] != source:
                    raise OperationError("stale_climate_source")
            if (len(existing.keys() | new_sources.keys()) > max_sources_per_key
                    or len(set(current.event_ids) | new_ids) > max_events_per_key):
                neutral = self.climate_invalidate_transaction(
                    db, key, actor="climate", reason="source_capacity",
                )
                assert neutral is not None
                return neutral
            return current

        return await self.transaction(prepare)

    @staticmethod
    def climate_assert_generation_transaction(
        db: sqlite3.Connection, key: tuple[str, str, str], generation: str,
        *, proof_now: float | None = None,
    ) -> None:
        row = db.execute(
            "SELECT generation FROM climate_states WHERE bot_id=? AND group_id=? AND user_id=?", key,
        ).fetchone()
        if row is None or row[0] != generation:
            raise OperationError("stale_climate_source")
        record = Store._climate_record_transaction(db, key)
        if record is None or not Store._climate_sources_current_transaction(db, record, proof_now):
            raise OperationError("stale_climate_source")

    async def climate_read(
        self, key: tuple[str, str, str], *, proof_now: float | None = None, max_keys: int = 4096,
    ) -> ClimateStateRecord:
        """Read or create a body-free epoch, keeping active and neutral keys bounded."""
        _validate_climate_key(key)
        self._validate_climate_scope(key)
        if type(max_keys) is not int or not 1 <= max_keys <= 4096:
            raise OperationError("invalid_climate_limits")

        def read(db: sqlite3.Connection) -> ClimateStateRecord:
            db.execute("BEGIN IMMEDIATE")
            record = self._climate_record_transaction(db, key)
            if record is None:
                record = self._climate_neutral_transaction(db, key)
            elif not self._climate_sources_current_transaction(db, record, proof_now):
                record = self.climate_invalidate_transaction(
                    db, key, actor="climate", reason="source_expired",
                )
                assert record is not None
            evicted_keys = self._climate_trim_keys_transaction(db, key, max_keys)
            return replace(record, evicted_keys=evicted_keys)

        return await self.transaction(read)

    async def climate_commit(
        self,
        key: tuple[str, str, str],
        *,
        values: Mapping[str, float],
        baselines: Mapping[str, float],
        last_update: float,
        expected_revision: int,
        expected_generation: str,
        source_status: str,
        source_refs: Sequence[str] = (),
        events: Sequence[ClimateEvent],
        sources: Sequence[ClimateSourceProof],
        proof_now: float | None = None,
        authorize: Callable[[sqlite3.Connection], object] | None = None,
        max_events_per_key: int = 256,
        max_sources_per_key: int = 32,
        max_keys: int = 4096,
    ) -> tuple[ClimateStateRecord, bool]:
        """Atomically CAS and append a Climate state plus identity-only events.

        The boolean is true only when this call accepted a new event set. A retry
        of the complete retained identity set returns the stored record and false.
        Proof or event eviction invalidates the entire derived key; callers use
        ``climate_prepare`` before computing values from a fresh default epoch.
        """
        _validate_climate_key(key)
        self._validate_climate_scope(key)
        _climate_identity(expected_generation, limit=128, code="stale_climate_source")
        if set(values) != set(_CLIMATE_DIMENSIONS) or set(baselines) != set(_CLIMATE_DIMENSIONS):
            raise OperationError("invalid_climate_state")
        normalized_values = {
            dimension: _climate_float(values[dimension]) for dimension in _CLIMATE_DIMENSIONS
        }
        normalized_baselines = {
            dimension: _climate_float(baselines[dimension]) for dimension in _CLIMATE_DIMENSIONS
        }
        normalized_last_update = _climate_float(last_update, bounded=False)
        if type(expected_revision) is not int or expected_revision < 0:
            raise OperationError("climate_revision_conflict")
        if source_status not in _CLIMATE_STATUSES:
            raise OperationError("invalid_climate_state")
        if type(max_events_per_key) is not int or max_events_per_key < 1:
            raise OperationError("invalid_climate_limits")
        if type(max_sources_per_key) is not int or max_sources_per_key < 1:
            raise OperationError("invalid_climate_limits")
        if type(max_keys) is not int or max_keys < 1:
            raise OperationError("invalid_climate_limits")
        normalized_refs = _climate_refs(source_refs, limit=max_sources_per_key)
        if not events:
            raise OperationError("invalid_climate_event")
        normalized_events = tuple(events)
        source_map, identities = self._climate_batch(
            key, sources, normalized_events, max_sources_per_key, max_events_per_key,
        )

        def commit(db: sqlite3.Connection) -> tuple[ClimateStateRecord, bool] | None:
            db.execute("BEGIN IMMEDIATE")
            if authorize is not None:
                authorize(db)
            current = self._climate_record_transaction(db, key)
            if (None if current is None else current.generation) != expected_generation:
                raise OperationError("stale_climate_source")
            assert current is not None
            as_of = self._climate_proof_time(proof_now)
            if not self._climate_sources_current_transaction(db, current, as_of):
                self.climate_invalidate_transaction(db, key, actor="climate", reason="source_expired")
                return None
            if any(not self._climate_source_current_transaction(db, source, as_of)
                   for source in source_map.values()):
                # This incoming proof has not contributed. A malformed or expired
                # new source cannot erase an otherwise healthy epoch.
                raise OperationError("stale_climate_source")
            existing_sources = {
                source.source_id: source for source in current.source_proofs
            }
            for source_id, source in source_map.items():
                if source_id in existing_sources and existing_sources[source_id] != source:
                    raise OperationError("stale_climate_source")
            generation = current.generation
            existing_events = set(current.event_ids)
            requested = set(identities)
            if existing_events & requested:
                if existing_events & requested != requested:
                    raise OperationError("climate_revision_conflict")
                return current, False
            current_revision = current.revision
            if current_revision != expected_revision:
                raise OperationError("climate_revision_conflict")
            if normalized_last_update < current.last_update:
                raise OperationError("climate_event_order")
            if (len(existing_sources.keys() | source_map.keys()) > max_sources_per_key
                    or len(existing_events | requested) > max_events_per_key):
                self.climate_invalidate_transaction(db, key, actor="climate", reason="source_capacity")
                return None
            next_revision = current_revision + len(normalized_events)
            columns = ",".join(_CLIMATE_DIMENSIONS)
            baseline_columns = ",".join(f"baseline_{dimension}" for dimension in _CLIMATE_DIMENSIONS)
            placeholders = ",".join("?" for _ in (*_CLIMATE_DIMENSIONS, *_CLIMATE_DIMENSIONS))
            update_columns = ",".join(
                [
                    *(f"{dimension}=excluded.{dimension}" for dimension in _CLIMATE_DIMENSIONS),
                    *(
                        f"baseline_{dimension}=excluded.baseline_{dimension}"
                        for dimension in _CLIMATE_DIMENSIONS
                    ),
                    "last_update=excluded.last_update",
                    "revision=excluded.revision",
                    "source_status=excluded.source_status",
                    "source_refs=excluded.source_refs",
                ]
            )
            db.execute(
                "INSERT INTO climate_states("
                "bot_id,group_id,user_id," + columns + "," + baseline_columns
                + ",last_update,revision,source_status,source_refs,generation) VALUES (?,?,?,"
                + placeholders + ",?,?,?,?,?) ON CONFLICT(bot_id,group_id,user_id) DO UPDATE SET "
                + update_columns,
                (
                    *key,
                    *(normalized_values[dimension] for dimension in _CLIMATE_DIMENSIONS),
                    *(normalized_baselines[dimension] for dimension in _CLIMATE_DIMENSIONS),
                    normalized_last_update,
                    next_revision,
                    source_status,
                    json.dumps(normalized_refs, ensure_ascii=False, separators=(",", ":")),
                    generation,
                ),
            )
            db.executemany(
                "INSERT INTO climate_events(bot_id,group_id,user_id,sensor,event_id,"
                "source_ref,observed_at,source_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [
                    (*key, event.sensor, event.event_id, event.source_ref, event.observed_at,
                     event.source.source_id if event.source is not None else "")
                    for event in normalized_events
                ],
            )
            db.executemany(
                "INSERT OR IGNORE INTO climate_sources(bot_id,group_id,user_id,generation,source_id,kind,"
                "origin_source_id,owner_id,owner_revision,author_id,retained_at,expires_at,"
                "event_id,receipt_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(*key, generation, source.source_id, source.kind, source.origin_source_id,
                  source.owner_id, source.owner_revision, source.author_id, source.retained_at,
                  source.expires_at, source.event_id, source.receipt_id) for source in source_map.values()],
            )
            evicted_keys = self._climate_trim_keys_transaction(db, key, max_keys)
            record = self._climate_record_transaction(db, key)
            if record is None:
                raise OperationError("invalid_climate_state")
            return replace(record, evicted_keys=tuple(evicted_keys)), True

        result = await self.transaction(commit)
        if result is None:
            raise OperationError("stale_climate_source")
        return result

    def _validate_climate_scope(self, key: tuple[str, str, str]) -> None:
        if self.bot_id is not None and key[0] != self.bot_id:
            raise OperationError("climate_scope_mismatch")

    async def settings_initialize(
        self, document: dict[str, JsonValue]
    ) -> tuple[int, dict[str, JsonValue]]:
        """Seed settings once, returning the existing current document on later starts."""
        encoded = _settings_json(document)
        normalized = _settings_document(encoded)

        def initialize(db: sqlite3.Connection) -> tuple[int, dict[str, JsonValue]]:
            row = _settings_row(db)
            if row is not None:
                return _settings_result(row)
            revision = 1
            db.execute(
                "INSERT INTO config_versions(revision,document) VALUES (?,?)", (revision, encoded)
            )
            self._settings_audit(db, revision, sorted(normalized), "initialized")
            return revision, normalized

        return await self.transaction(initialize)

    async def settings_current(self) -> tuple[int, dict[str, JsonValue]] | None:
        def current(db: sqlite3.Connection) -> tuple[int, dict[str, JsonValue]] | None:
            row = _settings_row(db)
            return None if row is None else _settings_result(row)

        return await self.transaction(current)

    async def settings_version(self, revision: int) -> dict[str, JsonValue] | None:
        if type(revision) is not int or revision < 1:
            return None

        def read(db: sqlite3.Connection) -> dict[str, JsonValue] | None:
            row = _settings_row(db, revision)
            return None if row is None else _settings_result(row)[1]

        return await self.transaction(read)

    async def settings_versions(self) -> list[int]:
        def read(db: sqlite3.Connection) -> list[int]:
            rows = db.execute("SELECT revision FROM config_versions ORDER BY revision").fetchall()
            revisions: list[int] = []
            for row in rows:
                revision = row[0]
                if type(revision) is not int or revision < 1:
                    raise OperationError("invalid_settings")
                revisions.append(revision)
            return revisions

        return await self.transaction(read)

    async def settings_save(
        self, document: dict[str, JsonValue], expected_revision: int,
        *, persona_receipt: SettingsPersonaReceipt | None = None,
    ) -> tuple[int, dict[str, JsonValue]]:
        """Append a settings revision with compare-and-swap and a value-free audit entry."""
        encoded = _settings_json(document)
        normalized = _settings_document(encoded)
        if type(expected_revision) is not int or expected_revision < 0:
            raise OperationError("revision_conflict")

        def save(db: sqlite3.Connection) -> tuple[int, dict[str, JsonValue]]:
            row = _settings_row(db)
            if row is None:
                if expected_revision != 0:
                    raise OperationError("revision_conflict")
                previous: dict[str, JsonValue] = {}
                current_revision = 0
            else:
                current_revision, previous = _settings_result(row)
                if current_revision != expected_revision:
                    raise OperationError("revision_conflict")
            revision = current_revision + 1
            changed = sorted(
                name
                for name in set(previous) | set(normalized)
                if previous.get(name, _MISSING) != normalized.get(name, _MISSING)
            )
            db.execute(
                "INSERT INTO config_versions(revision,document) VALUES (?,?)", (revision, encoded)
            )
            self._settings_audit(db, revision, changed, "saved", persona_receipt=persona_receipt)
            # Keep the initial revision plus the current revision and 31 immediately preceding ones.
            db.execute(
                "DELETE FROM config_versions WHERE revision < ? AND revision <> 1", (revision - 31,)
            )
            return revision, normalized

        return await self.transaction(save)

    async def settings_record_runtime_decision(
        self, document: dict[str, JsonValue], receipt: SettingsPersonaReceipt,
    ) -> SettingsPersonaReceipt:
        """Bind one request and its terminal decision to a retained config revision."""
        payload = cast(dict[str, JsonValue], asdict(receipt))

        def record(db: sqlite3.Connection) -> SettingsPersonaReceipt:
            row = _settings_row(db, receipt.saved_revision)
            if row is None:
                raise OperationError("settings_version_not_found")
            if _settings_result(row)[1] != document:
                raise OperationError("settings_runtime_receipt_conflict")
            rows = db.execute(
                "SELECT code,details FROM audit WHERE kind='settings' AND identity=? "
                "AND code IN ('runtime_requested','runtime_applied','runtime_rejected') ORDER BY id",
                (receipt.request_id,),
            ).fetchall()
            previous: dict[str, JsonValue] | None = None
            terminal = False
            for prior in rows:
                details = _settings_document(prior["details"])
                stored = details.get("persona_receipt")
                if not isinstance(stored, dict):
                    raise OperationError("invalid_settings_receipt")
                if prior["code"] == "runtime_" + receipt.decision:
                    if stored != payload:
                        raise OperationError("idempotency_conflict")
                    return receipt
                if prior["code"] == "runtime_requested":
                    previous = stored
                else:
                    terminal = True
            if receipt.decision == "requested":
                if rows:
                    raise OperationError("idempotency_conflict")
                current = _settings_row(db)
                if current is None or _settings_result(current)[0] != receipt.saved_revision:
                    raise OperationError("revision_conflict")
            else:
                if previous is None or terminal:
                    raise OperationError("settings_runtime_receipt_conflict")
                for name in payload.keys() - {"decision", "effective_revision"}:
                    if previous.get(name) != payload[name]:
                        raise OperationError("idempotency_conflict")
                if (
                    receipt.decision == "rejected"
                    and previous["effective_revision"] != receipt.effective_revision
                ):
                    raise OperationError("settings_runtime_receipt_conflict")
            self._settings_audit(
                db, receipt.saved_revision, [], "runtime_" + receipt.decision,
                persona_receipt=receipt, identity=receipt.request_id,
            )
            return receipt

        return await self.transaction(record)

    def _settings_audit(
        self, db: sqlite3.Connection, revision: int, changed_fields: list[str], code: str,
        *, persona_receipt: SettingsPersonaReceipt | None = None, identity: str | None = None,
    ) -> None:
        values: dict[str, object] = {"changed_fields": changed_fields}
        if persona_receipt is not None:
            values["persona_receipt"] = asdict(persona_receipt)
        details = json.dumps(values, separators=(",", ":"))
        db.execute(
            "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('settings',?,?,?,?)",
            (identity if identity is not None else self.instance_id or "standalone",
             revision, code, details),
        )

    def _validate_contact_input(self, contact: BotContactInput) -> None:
        if (self.bot_id is not None and contact.scope.bot_id != self.bot_id
                or contact.purpose not in {"autonomous_chat", "role_life_broadcast"}
                or contact.cause_kind not in {"conversation_context", "role_life"}
                or contact.target_user_id.startswith("system:")
                or contact.authority.scope != contact.scope
                or contact.authority.target_user_id != contact.target_user_id
                or contact.authority.purpose != contact.purpose
                or type(contact.authority.policy_revision) is not int
                or contact.authority.policy_revision < 0):
            raise OperationError("invalid_contact_input")
        for value, limit in ((contact.request_id, 128), (contact.target_user_id, 64),
                (contact.cause_id, 512), (contact.cause_version, 128),
                (contact.configuration_version, 128), (contact.authority.user_generation, 128),
                (contact.authority.group_generation, 128)):
            if not value or value != value.strip() or len(value) > limit:
                raise OperationError("invalid_contact_input")
        if (not math.isfinite(contact.created_at) or not math.isfinite(contact.expires_at)
                or not 0 <= contact.created_at < contact.expires_at):
            raise OperationError("invalid_contact_input")

    @staticmethod
    def _contact_record_transaction(db: sqlite3.Connection, request_id: str) -> ContactRequestRecord:
        row = db.execute("SELECT c.*,r.digest,r.origin_kind FROM contact_requests c "
            "JOIN requests r ON r.id=c.request_id WHERE c.request_id=?", (request_id,)).fetchone()
        if row is None:
            raise OperationError("unknown_contact_request")
        scope = Scope(bot_id=row["bot_id"], group_id=row["group_id"])
        contact = BotContactInput(row["request_id"], scope, row["target_user_id"], row["purpose"],
            ContactAuthorityProof(row["authority_revision"], scope, row["target_user_id"], row["purpose"],
                row["user_generation"], row["group_generation"]), row["cause_kind"], row["cause_id"],
            row["cause_version"], row["configuration_version"], row["created_at"], row["expires_at"])
        if row["origin_kind"] != "bot_contact" or row["digest"] != contact_input_digest(contact):
            raise OperationError("invalid_contact_request")
        return ContactRequestRecord(contact, row["revision"], bool(row["active"]), row["decision"],
            row["reason_code"], row["due_at"], row["decision_started_at"], row["send_started_at"])

    async def claim_contact_request(self, contact: BotContactInput, *,
        authorize: Callable[[sqlite3.Connection], object], now: float | None = None,
    ) -> tuple[str, ContactRequestRecord]:
        """Caller holds Policy dispatch boundary; claim an honest Bot request once."""
        self._validate_contact_input(contact)
        digest = contact_input_digest(contact)

        def claim(db: sqlite3.Connection) -> tuple[str, ContactRequestRecord]:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT digest,origin_kind FROM requests WHERE id=?",
                (contact.request_id,)).fetchone()
            if existing is not None:
                if existing["origin_kind"] != "bot_contact" or existing["digest"] != digest:
                    raise OperationError("idempotency_conflict")
                return "duplicate", self._contact_record_transaction(db, contact.request_id)
            cause = db.execute("SELECT request_id FROM contact_requests WHERE bot_id=? AND group_id=? "
                "AND cause_kind=? AND cause_id=? AND cause_version=?",
                (contact.scope.bot_id, contact.scope.group_id, contact.cause_kind,
                 contact.cause_id, contact.cause_version)).fetchone()
            if cause is not None:
                return "duplicate", self._contact_record_transaction(db, cause[0])
            if db.execute("SELECT 1 FROM contact_requests WHERE bot_id=? AND group_id=? AND active=1",
                    (contact.scope.bot_id, contact.scope.group_id)).fetchone() is not None:
                raise OperationError("contact_group_busy")
            as_of = time.time() if now is None else now
            if not math.isfinite(as_of) or not contact.created_at <= as_of < contact.expires_at:
                raise OperationError("contact_expired")
            authorize(db)
            db.execute("INSERT INTO requests(id,digest,state,origin_kind) "
                "VALUES (?,?,'accepted','bot_contact')", (contact.request_id, digest))
            db.execute("INSERT INTO contact_requests(request_id,bot_id,group_id,target_user_id,purpose,"
                "cause_kind,cause_id,cause_version,configuration_version,authority_revision,user_generation,"
                "group_generation,created_at,expires_at,revision,active) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1)",
                (contact.request_id, contact.scope.bot_id, contact.scope.group_id, contact.target_user_id,
                 contact.purpose, contact.cause_kind, contact.cause_id, contact.cause_version,
                 contact.configuration_version, contact.authority.policy_revision,
                 contact.authority.user_generation,
                 contact.authority.group_generation, contact.created_at, contact.expires_at))
            return "new", self._contact_record_transaction(db, contact.request_id)
        return await self.transaction(claim)

    async def read_contact_request(self, request_id: str) -> ContactRequestRecord:
        return await self.transaction(lambda db: self._contact_record_transaction(db, request_id))

    @staticmethod
    def assert_contact_request_transaction(db: sqlite3.Connection, call: ActionCall, *,
        stage: Literal["intent", "operation"], now: float | None = None,
    ) -> ContactRequestRecord:
        proof = call.contact
        if proof is None:
            raise OperationError("contact_proof_required")
        record = Store._contact_record_transaction(db, call.request_id)
        contact = record.contact
        if (proof.contact != contact or proof.revision != record.revision or not record.active
                or call.scope != contact.scope or call.subject != contact.target_user_id
                or call.contact_addressee is not None and (
                    call.contact_addressee.scope != contact.scope
                    or call.contact_addressee.user_id != contact.target_user_id)):
            raise OperationError("stale_contact_request")
        as_of = time.time() if now is None else now
        if not math.isfinite(as_of) or not contact.created_at <= as_of < contact.expires_at:
            raise OperationError("contact_expired")
        if db.execute("SELECT state FROM requests WHERE id=?", (call.request_id,)).fetchone()[0] not in {
                "accepted", "running", "queued"}:
            raise OperationError("stale_contact_request")
        if stage == "operation":
            action = db.execute("SELECT request_id,state,action,subject,bot_id,group_id "
                "FROM actions WHERE id=?",
                (call.key,)).fetchone()
            if action is None or tuple(action) != (call.request_id, "dispatching", call.action,
                    call.subject, contact.scope.bot_id, contact.scope.group_id):
                raise OperationError("stale_contact_action")
        if proof.phase == "timing":
            if (call.action != "model.invoke" or record.decision
                    or stage == "intent" and record.decision_started_at is not None
                    or stage == "operation" and record.decision_started_at is None):
                raise OperationError("contact_timing_used")
        elif proof.phase in {"reply", "tool", "send", "followup"}:
            if record.decision not in {"now", "defer"} or (
                    record.decision == "defer" and (record.due_at is None or as_of < record.due_at)):
                raise OperationError("contact_not_due")
        else:
            raise OperationError("invalid_contact_phase")
        return record

    @staticmethod
    def mark_contact_action_intent_transaction(
        db: sqlite3.Connection, call: ActionCall, *, now: float,
    ) -> None:
        assert call.contact is not None
        column = ("decision_started_at" if call.contact.phase == "timing" else
                  "send_started_at" if call.action == "message.reply" else None)
        if column is not None:
            db.execute(f"UPDATE contact_requests SET {column}=? WHERE request_id=? AND {column} IS NULL",
                (now, call.request_id))

    @staticmethod
    def assert_contact_usage_transaction(db: sqlite3.Connection, contact: BotContactInput, *,
        stage: Literal["decision", "send"], now: float, day_start: float, day_end: float,
        user_minimum_interval_seconds: float, user_day_limit: int,
        group_minimum_interval_seconds: float, group_day_limit: int,
    ) -> None:
        if (stage not in {"decision", "send"} or not all(math.isfinite(v) for v in
                (now, day_start, day_end, user_minimum_interval_seconds, group_minimum_interval_seconds))
                or not day_start <= now < day_end
                or min(user_minimum_interval_seconds, group_minimum_interval_seconds) <= 0
                or type(user_day_limit) is not int or type(group_day_limit) is not int
                or min(user_day_limit, group_day_limit) < 1):
            raise OperationError("invalid_contact_limits")
        column = "decision_started_at" if stage == "decision" else "send_started_at"
        for identity_column, identity, interval, limit in (
                ("target_user_id", contact.target_user_id, user_minimum_interval_seconds, user_day_limit),
                ("group_id", contact.scope.group_id, group_minimum_interval_seconds, group_day_limit)):
            count, latest = db.execute(f"SELECT count(CASE WHEN {column}>=? AND {column}<? THEN 1 END),"
                f"max({column}) FROM contact_requests WHERE bot_id=? AND {identity_column}=? "
                "AND request_id<>?", (day_start, day_end, contact.scope.bot_id, identity,
                                      contact.request_id)).fetchone()
            if count >= limit or latest is not None and now - latest < interval:
                raise OperationError("contact_" + stage + "_limit")

    async def decide_contact_request(self, contact: BotContactInput, *, expected_revision: int,
        decision: Literal["now", "defer", "abandon"], reason_code: str, due_at: float | None = None,
        now: float | None = None, authorize: Callable[[sqlite3.Connection], object] | None = None,
    ) -> ContactRequestRecord:
        """Commit one timing choice under the caller's Policy dispatch boundary."""
        if (decision not in {"now", "defer", "abandon"} or not reason_code
                or reason_code != reason_code.strip()):
            raise OperationError("invalid_contact_decision")
        def decide(db: sqlite3.Connection) -> ContactRequestRecord:
            db.execute("BEGIN IMMEDIATE")
            record = self._contact_record_transaction(db, contact.request_id)
            as_of = time.time() if now is None else now
            if (record.contact != contact or record.revision != expected_revision or not record.active
                    or record.decision or record.decision_started_at is None):
                raise OperationError("stale_contact_request")
            if not math.isfinite(as_of) or not contact.created_at <= as_of < contact.expires_at:
                raise OperationError("contact_expired")
            if ((decision == "defer" and (due_at is None or not math.isfinite(due_at)
                    or not as_of < due_at < contact.expires_at))
                    or decision != "defer" and due_at is not None):
                raise OperationError("invalid_contact_decision")
            if authorize is not None:
                authorize(db)
            db.execute("UPDATE contact_requests SET revision=revision+1,decision=?,reason_code=?,"
                "due_at=?,active=? WHERE request_id=?",
                (decision, reason_code, due_at, int(decision != "abandon"), contact.request_id))
            if decision == "abandon":
                self._finish_request_transaction(
                    db, contact.request_id, "cancelled_before_dispatch", reason_code)
            return self._contact_record_transaction(db, contact.request_id)
        return await self.transaction(decide)

    async def close_contact_request(self, contact: BotContactInput, *, expected_revision: int,
        state: str, code: str = "",
    ) -> ContactRequestRecord:
        """Close the lifecycle while preserving the existing request outcome owner."""
        if state not in {"succeeded", "failed", "unknown", "cancelled_before_dispatch"}:
            raise OperationError("invalid_contact_state")
        def close(db: sqlite3.Connection) -> ContactRequestRecord:
            db.execute("BEGIN IMMEDIATE")
            record = self._contact_record_transaction(db, contact.request_id)
            if record.contact != contact or record.revision != expected_revision or not record.active:
                raise OperationError("stale_contact_request")
            if state == "succeeded":
                total = db.execute("SELECT total FROM deliveries WHERE request_id=?",
                    (contact.request_id,)).fetchone()
                sent = db.execute("SELECT state,receipt FROM actions WHERE request_id=? AND action IN (?,?)",
                    (contact.request_id, *_DELIVERY_ACTIONS)).fetchall()
                unsettled = db.execute("SELECT 1 FROM actions WHERE request_id=? "
                    "AND state IN ('unknown','dispatching') LIMIT 1", (contact.request_id,)).fetchone()
                if (total is None or len(sent) != total[0] or not sent or unsettled is not None
                        or any(row[0] != "succeeded" or not row[1] for row in sent)):
                    raise OperationError("incomplete_contact_delivery")
            self._finish_request_transaction(db, contact.request_id, state, code)
            db.execute("UPDATE contact_requests SET revision=revision+1,active=0,reason_code=? "
                "WHERE request_id=?",
                (code, contact.request_id))
            return self._contact_record_transaction(db, contact.request_id)
        return await self.transaction(close)

    async def assert_unclaimed_request(self, event: Event) -> None:
        """Read the durable ingress identity before a caller mutates transient state.

        The eventual claim remains atomic; concurrent late claim conflicts still
        require the caller to discard its transient contribution.
        """
        row = await self.transaction(lambda db: db.execute(
            "SELECT digest,origin_kind FROM requests WHERE id=?", (event.event_id,),
        ).fetchone())
        if row is not None:
            if row["origin_kind"] != "inbound_event" or row["digest"] != request_digest(event):
                raise OperationError("idempotency_conflict")
            raise OperationError("duplicate")

    async def claim_request(self, event: Event) -> str:
        digest = request_digest(event)

        def claim(db: sqlite3.Connection) -> str:
            row = db.execute("SELECT digest,origin_kind FROM requests WHERE id=?",
                (event.event_id,)).fetchone()
            if row is not None:
                if row["origin_kind"] != "inbound_event" or row["digest"] != digest:
                    raise OperationError("idempotency_conflict")
                return "duplicate"
            db.execute(
                "INSERT INTO requests(id,digest,state) VALUES (?,?,'accepted')", (event.event_id, digest)
            )
            db.execute(
                "INSERT INTO request_sources(owner_request_id,source_request_id,source_order) "
                "VALUES (?,?,0)",
                (event.event_id, event.event_id),
            )
            return "new"

        return await self.transaction(claim)

    async def merge_request_sources(
        self, owner_request_id: str, source_request_ids: list[str]
    ) -> list[str]:
        """Assign a complete ordered source set before any source has dispatched.

        The caller must serialize this with action dispatch using the policy dispatch
        boundary. The persisted state checks below reject already-dispatched and
        uncertain requests; they cannot replace that cross-component ordering.
        """
        if (
            not owner_request_id
            or not source_request_ids
            or owner_request_id not in source_request_ids
            or any(not source_id for source_id in source_request_ids)
            or len(set(source_request_ids)) != len(source_request_ids)
        ):
            raise OperationError("invalid_merge_sources")

        def merge(db: sqlite3.Connection) -> list[str]:
            placeholders = ",".join("?" for _ in source_request_ids)
            rows = db.execute(f"SELECT id,state,origin_kind FROM requests WHERE id IN ({placeholders})",
                source_request_ids).fetchall()
            if any(row["origin_kind"] != "inbound_event" for row in rows):
                raise OperationError("contact_merge_forbidden")
            request_states = {str(row["id"]): str(row["state"]) for row in rows}
            if set(request_states) != set(source_request_ids):
                raise OperationError("unknown_request")
            if any(state not in {"accepted", "running", "queued"} for state in request_states.values()):
                raise OperationError("merge_after_dispatch")

            action_states = db.execute(
                f"SELECT request_id,state FROM actions WHERE request_id IN ({placeholders}) "
                "AND action <> 'model.invoke'",
                source_request_ids,
            ).fetchall()
            no_dispatch_states = {"queued", "denied", "cancelled_before_dispatch"}
            if any(str(row["state"]) not in no_dispatch_states for row in action_states):
                raise OperationError("merge_after_dispatch")

            existing_owners = {
                str(row[0])
                for row in db.execute(
                    "SELECT DISTINCT owner_request_id FROM request_sources "
                    f"WHERE source_request_id IN ({placeholders})",
                    source_request_ids,
                )
            }
            if not existing_owners:
                raise OperationError("unknown_request_source")
            mapped_sources: set[str] = set()
            for existing_owner in existing_owners:
                owned = {
                    str(row[0])
                    for row in db.execute(
                        "SELECT source_request_id FROM request_sources WHERE owner_request_id=?",
                        (existing_owner,),
                    )
                }
                if not owned <= set(source_request_ids):
                    raise OperationError("incomplete_merge_sources")
                mapped_sources.update(owned)
            if mapped_sources != set(source_request_ids):
                raise OperationError("unknown_request_source")

            current = [
                str(row[0])
                for row in db.execute(
                    "SELECT source_request_id FROM request_sources "
                    "WHERE owner_request_id=? ORDER BY source_order",
                    (owner_request_id,),
                )
            ]
            if current == source_request_ids:
                return current

            for existing_owner in existing_owners:
                db.execute(
                    "DELETE FROM request_sources WHERE owner_request_id=?", (existing_owner,)
                )
            db.executemany(
                "INSERT INTO request_sources(owner_request_id,source_request_id,source_order) "
                "VALUES (?,?,?)",
                [
                    (owner_request_id, source_id, order)
                    for order, source_id in enumerate(source_request_ids)
                ],
            )
            return list(source_request_ids)

        return await self.transaction(merge)

    async def request_source_ids(self, owner_request_id: str) -> list[str]:
        def read(db: sqlite3.Connection) -> list[str]:
            if db.execute("SELECT 1 FROM requests WHERE id=?", (owner_request_id,)).fetchone() is None:
                raise OperationError("unknown_request")
            return [
                str(row[0])
                for row in db.execute(
                    "SELECT source_request_id FROM request_sources "
                    "WHERE owner_request_id=? ORDER BY source_order",
                    (owner_request_id,),
                )
            ]

        return await self.transaction(read)

    async def finish_request(self, request_id: str, state: str, code: str = "") -> str:
        def finish(db: sqlite3.Connection) -> str:
            return self._finish_request_transaction(db, request_id, state, code)

        return await self.transaction(finish)

    @staticmethod
    def _finish_request_transaction(
        db: sqlite3.Connection, request_id: str, state: str, code: str
    ) -> str:
        aggregate = state
        if state not in {"accepted", "running", "queued", "succeeded"}:
            children = {
                str(row[0])
                for row in db.execute("SELECT state FROM actions WHERE request_id=?", (request_id,))
            }
            storage_uncertain = code in {
                "unknown",
                "audit_failed",
                "storage_failed",
                "storage_unavailable",
            }
            if children & {"unknown", "dispatching"} or storage_uncertain:
                aggregate = "unknown"
            elif code in {"cancelled", "deadline", "revoked"} or state == "cancelled_before_dispatch":
                dispatched = children - {"queued", "denied", "cancelled_before_dispatch"}
                if not dispatched:
                    aggregate = "cancelled_before_dispatch"
                elif state in {"unknown", "cancelled_before_dispatch"}:
                    aggregate = "failed"
            elif state == "unknown" and children:
                # Settled children cannot become uncertain merely because a local handler failed.
                aggregate = "failed"
        db.execute("UPDATE requests SET state=?,code=? WHERE id=?", (aggregate, code, request_id))
        return aggregate

    async def finish_merged_request(self, owner_request_id: str, state: str, code: str = "") -> str:
        """Settle the owner and every source request while preserving per-request outcomes."""
        def finish(db: sqlite3.Connection) -> str:
            if db.execute(
                "SELECT 1 FROM requests WHERE id=?", (owner_request_id,)
            ).fetchone() is None:
                raise OperationError("unknown_request")
            aggregate = self._finish_request_transaction(db, owner_request_id, state, code)
            source_ids = [
                str(row[0])
                for row in db.execute(
                    "SELECT source_request_id FROM request_sources WHERE owner_request_id=?",
                    (owner_request_id,),
                )
            ]
            for source_id in source_ids:
                if source_id == owner_request_id:
                    continue
                source_state, source_code = (
                    ("superseded", f"merged_into:{owner_request_id}")
                    if aggregate == "succeeded"
                    else (aggregate, code)
                )
                db.execute(
                    "UPDATE requests SET state=?,code=? WHERE id=?",
                    (source_state, source_code, source_id),
                )
            return aggregate

        return await self.transaction(finish)

    async def begin_delivery(self, request_id: str, total: int) -> None:
        if type(total) is not int or not 1 <= total <= 8:
            raise OperationError("output_limit")

        def begin(db: sqlite3.Connection) -> None:
            if db.execute("SELECT 1 FROM requests WHERE id=?", (request_id,)).fetchone() is None:
                raise OperationError("unknown_request")
            if db.execute("SELECT 1 FROM deliveries WHERE request_id=?", (request_id,)).fetchone():
                raise OperationError("duplicate")
            db.execute("INSERT INTO deliveries VALUES (?,?)", (request_id, total))

        await self.transaction(begin)

    @staticmethod
    def append_delivery_transaction(
        db: sqlite3.Connection, request_id: str, expected_total: int,
        additional: int, limit: int,
    ) -> None:
        """Append a generated phase in the same transaction as its first send intent."""
        if not 1 <= additional or not expected_total + additional <= limit <= 8:
            raise OperationError("output_limit")
        row = db.execute("SELECT total FROM deliveries WHERE request_id=?", (request_id,)).fetchone()
        if row is None or row["total"] != expected_total:
            raise OperationError("stale_delivery")
        receipts = db.execute(
            "SELECT state,receipt FROM actions WHERE request_id=? AND action IN (?,?)",
            (request_id, *_DELIVERY_ACTIONS),
        ).fetchall()
        if len(receipts) != expected_total or any(
            row["state"] != "succeeded" or not row["receipt"] for row in receipts
        ):
            raise OperationError("incomplete_primary")
        db.execute(
            "UPDATE deliveries SET total=? WHERE request_id=?", (expected_total + additional, request_id)
        )

    async def record_followup(self, request_id: str, ordinal: int, code: str) -> None:
        await self.transaction(lambda db: db.execute(
            "INSERT INTO audit(kind,identity,revision,code,details) VALUES ('followup',?,0,?,?)",
            (request_id, code, json.dumps({"ordinal": ordinal}, separators=(",", ":"))),
        ))

    async def finalize_delivery_total(self, request_id: str, total: int) -> None:
        """Narrow a reserved streaming batch after its final segment is known."""
        if type(total) is not int or not 1 <= total <= 8:
            raise OperationError("output_limit")

        def finalize(db: sqlite3.Connection) -> None:
            row = db.execute(
                "SELECT total FROM deliveries WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise OperationError("unknown_delivery")
            reserved = int(row["total"])
            attempted = int(
                db.execute(
                    "SELECT count(*) FROM actions WHERE request_id=? "
                    "AND action IN (?,?)",
                    (request_id, *_DELIVERY_ACTIONS),
                ).fetchone()[0]
            )
            if total > reserved or total < attempted:
                raise OperationError("invalid_delivery_total")
            db.execute(
                "UPDATE deliveries SET total=? WHERE request_id=?", (total, request_id)
            )

        await self.transaction(finalize)

    @staticmethod
    def assert_successful_reply_transaction(
        db: sqlite3.Connection, *, bot_id: str, group_id: str | None = None,
        private_user_id: str | None = None, subject_id: str,
        event_id: str, source_digest: str, action_id: str, receipt_id: str,
    ) -> None:
        """Exact durable reply/source binding; partial/unknown delivery is no proof."""
        row = db.execute(
            "SELECT a.*,r.digest AS source_digest FROM actions a "
            "JOIN request_sources rs ON rs.owner_request_id=a.request_id "
            "JOIN requests r ON r.id=rs.source_request_id "
            "WHERE a.id=? AND rs.source_request_id=?",
            (action_id, event_id),
        ).fetchone()
        if row is None:
            raise OperationError("reply_receipt_missing")
        if row["source_digest"] != source_digest:
            raise OperationError("reply_source_changed")
        scope_matches = (
            row["scope_kind"] == "group" and row["group_id"] == group_id
            and private_user_id is None and row["private_user_id"] is None
            if group_id is not None else
            row["scope_kind"] == "private" and row["group_id"] is None
            and row["private_user_id"] == private_user_id and private_user_id == subject_id
        )
        if (row["bot_id"] != bot_id or not scope_matches
                or row["subject"] != subject_id or row["action"] != "message.reply"
                or row["state"] != "succeeded" or not receipt_id
                or row["receipt"] != receipt_id):
            raise OperationError("reply_receipt_not_succeeded")
        delivery = db.execute("SELECT total FROM deliveries WHERE request_id=?",
                              (row["request_id"],)).fetchone()
        if delivery is None or Store.delivery_transaction(
            db, str(row["request_id"]), int(delivery["total"])
        )["state"] != "complete":
            raise OperationError("reply_delivery_incomplete")

    @staticmethod
    def delivery_transaction(
        db: sqlite3.Connection, request_id: str, total: int
    ) -> dict[str, JsonValue]:
        states = [
            str(row[0])
            for row in db.execute(
                "SELECT state FROM actions WHERE request_id=? AND action IN (?,?)",
                (request_id, *_DELIVERY_ACTIONS),
            )
        ]
        sent = states.count("succeeded")
        request = db.execute("SELECT state FROM requests WHERE id=?", (request_id,)).fetchone()
        if sent == total:
            state = "complete"
        elif "unknown" in states:
            state = "partial_unknown" if sent else "unknown"
        elif request is not None and request[0] not in {"accepted", "running", "queued"}:
            state = "partial_failed" if sent else "not_sent"
        else:
            state = "partial" if sent else "pending"
        return {"request_id": request_id, "total": total, "sent": sent, "state": state}

    async def status(self) -> dict[str, JsonValue]:
        def read(db: sqlite3.Connection) -> dict[str, JsonValue]:
            actions: dict[str, JsonValue] = {
                str(row[0]): int(row[1])
                for row in db.execute("SELECT state,count(*) FROM actions GROUP BY state")
            }
            requests: dict[str, JsonValue] = {
                str(row[0]): int(row[1])
                for row in db.execute("SELECT state,count(*) FROM requests GROUP BY state")
            }
            recent: list[JsonValue] = [
                dict(row)
                for row in db.execute(
                    "SELECT id AS key,request_id,action,state,revision,code "
                    "FROM actions ORDER BY rowid DESC LIMIT 10"
                )
            ]
            return {
                "actions": actions,
                "requests": requests,
                "storage_errors": self.storage_errors,
                "recent": recent,
                "deliveries": [
                    self.delivery_transaction(db, str(row[0]), int(row[1]))
                    for row in db.execute(
                        "SELECT request_id,total FROM deliveries ORDER BY rowid DESC LIMIT 10"
                    ).fetchall()
                ],
            }

        return await self.transaction(read)

    async def diagnostics(self) -> dict[str, JsonValue]:
        """Read bounded traces and aggregate reported tokens without reading text."""
        def read(db: sqlite3.Connection) -> dict[str, JsonValue]:
            # Multiple terminal records are possible after cancellation/quarantine.
            # Use the latest measurement, not every audit transition.
            measured = """
                SELECT a.rowid AS sequence,a.id,a.request_id,a.action,a.state,a.code,
                       CASE WHEN json_valid(t.details) THEN t.details
                            WHEN json_valid(d.details) THEN d.details ELSE '{}' END AS details
                FROM actions a LEFT JOIN audit t ON t.id=(
                    SELECT max(x.id) FROM audit x WHERE x.identity=a.id
                    AND x.kind='terminal' AND x.details<>''
                )
                LEFT JOIN audit d ON d.id=(
                    SELECT max(x.id) FROM audit x WHERE x.identity=a.id
                    AND x.kind='dispatch' AND x.details<>''
                )
            """
            row = db.execute("""SELECT count(*) AS model_calls,
                sum(json_type(details,'$.usage')='object') AS reported_calls,
                sum(coalesce(json_extract(details,'$.usage.input_tokens'),0)) AS input_tokens,
                sum(coalesce(json_extract(details,'$.usage.output_tokens'),0)) AS output_tokens,
                sum(coalesce(json_extract(details,'$.usage.cached_input_tokens'),0)) AS cached_input_tokens,
                sum(coalesce(json_extract(details,'$.usage.cache_write_tokens'),0)) AS cache_write_tokens
                FROM (""" + measured + """)
                WHERE action IN ('model.invoke','model.schedule','model.dream')
                """).fetchone()
            usage: dict[str, JsonValue] = {key: int(row[key] or 0) for key in row.keys()}
            traces: list[JsonValue] = []
            # Background model operations have durable Actions, not fabricated
            # conversation requests. Keep their read-only groups distinct even
            # if a request ID happens to match a conversation ID.
            heads = list(db.execute(
                "SELECT id,state,code,'conversation' AS origin FROM requests "
                "ORDER BY rowid DESC LIMIT 20"
            ))
            heads.extend(db.execute("""
                SELECT a.request_id AS id,a.state,a.code,'background' AS origin
                FROM actions a WHERE a.action IN ('model.schedule','model.dream')
                AND a.rowid=(SELECT max(b.rowid) FROM actions b
                    WHERE b.request_id=a.request_id AND b.action IN ('model.schedule','model.dream'))
                ORDER BY a.rowid DESC LIMIT 20
            """))
            background_traces: list[JsonValue] = []
            for request in heads:
                children: list[JsonValue] = []
                action_kind = (
                    "action IN ('model.schedule','model.dream')"
                    if request["origin"] == "background"
                    else "action NOT IN ('model.schedule','model.dream')"
                )
                for action in db.execute(
                    "SELECT * FROM (" + measured + ") WHERE request_id=? AND "
                    + action_kind + " ORDER BY sequence LIMIT 32",
                    (request["id"],),
                ):
                    details = json.loads(action["details"])
                    children.append({
                        "key": action["id"], "action": action["action"], "state": action["state"],
                        "code": action["code"], "task": details.get("task", ""),
                        "elapsed_ms": details.get("elapsed_ms"), "usage": details.get("usage"),
                        "first_delta_ms": details.get("first_delta_ms"),
                        "provider": details.get("provider"), "model": details.get("model"),
                        "pricing": details.get("pricing"),
                    })
                target = background_traces if request["origin"] == "background" else traces
                block_traces: list[JsonValue] = [json.loads(row["details"]) for row in db.execute(
                    "SELECT details FROM audit WHERE kind='block_trace' "
                    "AND json_extract(details,'$.request_id')=? ORDER BY id DESC LIMIT 32",
                    (request["id"],),
                )] if request["origin"] == "conversation" else []
                target.append({"request_id": request["id"], "state": request["state"],
                               "code": request["code"], "origin": request["origin"],
                               "actions": children, "block_traces": block_traces})
            return {"usage": usage, "traces": traces, "background_traces": background_traces}
        return await self.transaction(read)

    async def backup(self, destination: Path) -> None:
        def copy(db: sqlite3.Connection) -> None:
            if destination.resolve() == self.path.resolve() or destination.exists():
                raise OperationError("backup_destination_exists")
            target = sqlite3.connect(destination)
            try:
                db.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise OperationError("backup_invalid")
            finally:
                target.close()

        await self.transaction(copy)


def action_digest(values: list[str | bool]) -> str:
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


async def drain_on_cancel[T](task: asyncio.Task[T]) -> T:
    """Do not abandon owner work even if a caller cancels repeatedly."""
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
