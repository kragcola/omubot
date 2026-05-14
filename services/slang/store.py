"""SQLite store for Omubot group slang terms."""

from __future__ import annotations

import contextlib
import json
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

from services.learning_normalizer import LearningNormalizerStore, normalize_key, score_similarity
from services.similarity import NgramSimilarityProvider
from services.slang.drift_reviewer import SlangDriftAssessment
from services.slang.quality import matches_slang_candidate
from services.slang.types import (
    VALID_REPEAT_POLICIES,
    VALID_SCOPES,
    VALID_STATUSES,
    RepeatPolicy,
    SlangDriftReview,
    SlangExtractionRun,
    SlangObservation,
    SlangPendingCandidate,
    SlangScope,
    SlangSettings,
    SlangStatus,
    SlangTerm,
    SlangTermRevision,
)
from services.storage import connect_sqlite

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CACHE_TTL_S = 5.0


@dataclass(slots=True)
class _SettingsCacheEntry:
    settings: SlangSettings
    cached_at: float


@dataclass(slots=True)
class _TermSnapshotCacheEntry:
    terms: list[SlangTerm]
    cached_at: float


_SETTINGS_CACHE: dict[str, _SettingsCacheEntry] = {}
_TERM_SNAPSHOT_CACHE: dict[tuple[str, str], _TermSnapshotCacheEntry] = {}
_DRIFT_LEXICAL_SAME_THRESHOLD = 0.28
_AI_REJECT_REOBSERVE_COUNT_THRESHOLD = 3
_AI_REJECT_REOBSERVE_USER_THRESHOLD = 2
_AI_REJECT_REOBSERVE_BACKFILL_VERSION = 1
_AI_REJECT_REOBSERVE_MAX_TRACKED_MESSAGES = 100
_AI_REJECT_REOBSERVE_MAX_EVIDENCE_CHARS = 500


@dataclass(slots=True)
class _DriftReviewOutcome:
    drift_id: str | None = None
    verdict: str = "not_applicable"
    confidence: float = 0.0
    reason: str = ""
    meaning_similarity: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def opens_drift(self) -> bool:
        return bool(self.drift_id)

    @property
    def allows_alias_merge(self) -> bool:
        return self.verdict in {"not_applicable", "alias_candidate"}

    @property
    def allows_meaning_update(self) -> bool:
        return self.verdict == "not_applicable"


class SlangDatabaseCorruptError(RuntimeError):
    """Raised when the slang SQLite database fails health checks."""

    def __init__(self, db_path: str | Path, detail: str) -> None:
        self.db_path = str(db_path)
        self.detail = str(detail).strip() or "database integrity check failed"
        super().__init__(
            f"slang database corrupt | db={self.db_path} "
            f"error={self.detail} repair=scripts/dev/slang_db_repair.py"
        )

_CREATE_TERMS = """\
CREATE TABLE IF NOT EXISTS slang_terms (
    term_id            TEXT PRIMARY KEY,
    term_key           TEXT NOT NULL,
    term               TEXT NOT NULL,
    meaning            TEXT NOT NULL DEFAULT '',
    aliases_json       TEXT NOT NULL DEFAULT '[]',
    scope              TEXT NOT NULL DEFAULT 'group',
    group_id           TEXT NOT NULL DEFAULT '',
    confidence         REAL NOT NULL DEFAULT 0.0,
    status             TEXT NOT NULL DEFAULT 'candidate',
    usage_count        INTEGER NOT NULL DEFAULT 0,
    unique_users_json  TEXT NOT NULL DEFAULT '[]',
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL,
    last_inferred_at   TEXT,
    source             TEXT NOT NULL DEFAULT 'extractor',
    repeat_policy      TEXT NOT NULL DEFAULT 'understand_only',
    notes              TEXT NOT NULL DEFAULT '',
    meta_json          TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
)"""

_CREATE_OBSERVATIONS = """\
CREATE TABLE IF NOT EXISTS slang_observations (
    observation_id TEXT PRIMARY KEY,
    term_id        TEXT NOT NULL,
    group_id       TEXT NOT NULL,
    user_id        TEXT NOT NULL DEFAULT '',
    message_id     INTEGER,
    raw_text       TEXT NOT NULL DEFAULT '',
    context        TEXT NOT NULL DEFAULT '',
    observed_at    TEXT NOT NULL,
    reason         TEXT NOT NULL DEFAULT ''
)"""

_CREATE_SETTINGS = """\
CREATE TABLE IF NOT EXISTS slang_settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""

_CREATE_PENDING = """\
CREATE TABLE IF NOT EXISTS slang_pending_candidates (
    pending_id        TEXT PRIMARY KEY,
    term_key          TEXT NOT NULL,
    term              TEXT NOT NULL,
    meaning           TEXT NOT NULL DEFAULT '',
    aliases_json      TEXT NOT NULL DEFAULT '[]',
    group_id          TEXT NOT NULL,
    confidence        REAL NOT NULL DEFAULT 0.0,
    count             INTEGER NOT NULL DEFAULT 0,
    unique_users_json TEXT NOT NULL DEFAULT '[]',
    evidence          TEXT NOT NULL DEFAULT '',
    reason            TEXT NOT NULL DEFAULT '',
    repeat_policy     TEXT NOT NULL DEFAULT 'understand_only',
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    meta_json         TEXT NOT NULL DEFAULT '{}'
)"""

_CREATE_PENDING_KEYS = """\
CREATE TABLE IF NOT EXISTS slang_pending_candidate_keys (
    pending_id TEXT NOT NULL,
    group_id   TEXT NOT NULL,
    term_key   TEXT NOT NULL,
    key_kind   TEXT NOT NULL DEFAULT 'alias',
    PRIMARY KEY (pending_id, term_key)
)"""

_CREATE_RUNS = """\
CREATE TABLE IF NOT EXISTS slang_extraction_runs (
    run_id              TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    status              TEXT NOT NULL DEFAULT 'running',
    group_count         INTEGER NOT NULL DEFAULT 0,
    scanned_messages    INTEGER NOT NULL DEFAULT 0,
    extracted_terms     INTEGER NOT NULL DEFAULT 0,
    promoted_candidates INTEGER NOT NULL DEFAULT 0,
    error               TEXT NOT NULL DEFAULT '',
    duration_ms         INTEGER NOT NULL DEFAULT 0,
    meta_json           TEXT NOT NULL DEFAULT '{}'
)"""

_CREATE_REVISIONS = """\
CREATE TABLE IF NOT EXISTS slang_term_revisions (
    revision_id TEXT PRIMARY KEY,
    term_id     TEXT NOT NULL,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'system',
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json  TEXT NOT NULL DEFAULT '{}',
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    meta_json   TEXT NOT NULL DEFAULT '{}'
)"""

_CREATE_DRIFT_REVIEWS = """\
CREATE TABLE IF NOT EXISTS slang_drift_reviews (
    drift_id     TEXT PRIMARY KEY,
    term_id      TEXT NOT NULL,
    term_key     TEXT NOT NULL,
    term         TEXT NOT NULL,
    group_id     TEXT NOT NULL DEFAULT '',
    old_meaning  TEXT NOT NULL DEFAULT '',
    new_meaning  TEXT NOT NULL DEFAULT '',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    evidence     TEXT NOT NULL DEFAULT '',
    confidence   REAL NOT NULL DEFAULT 0.0,
    reason       TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    meta_json    TEXT NOT NULL DEFAULT '{}'
)"""

_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_slang_term_scope ON slang_terms(term_key, scope, group_id)",
    "CREATE INDEX IF NOT EXISTS idx_slang_status ON slang_terms(status)",
    "CREATE INDEX IF NOT EXISTS idx_slang_group ON slang_terms(scope, group_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_slang_ob_term_time ON slang_observations(term_id, observed_at)",
    "CREATE INDEX IF NOT EXISTS idx_slang_ob_group_time ON slang_observations(group_id, observed_at)",
    "CREATE INDEX IF NOT EXISTS idx_slang_ob_msg ON slang_observations(term_id, message_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_slang_pending_scope ON slang_pending_candidates(term_key, group_id)",
    "CREATE INDEX IF NOT EXISTS idx_slang_pending_group ON slang_pending_candidates(group_id, count)",
    "CREATE INDEX IF NOT EXISTS idx_slang_pending_keys_lookup ON slang_pending_candidate_keys(group_id, term_key)",
    "CREATE INDEX IF NOT EXISTS idx_slang_pending_keys_pending ON slang_pending_candidate_keys(pending_id)",
    "CREATE INDEX IF NOT EXISTS idx_slang_runs_started ON slang_extraction_runs(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_slang_revisions_term ON slang_term_revisions(term_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_slang_drift_status ON slang_drift_reviews(status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_slang_drift_term ON slang_drift_reviews(term_id, status)",
]


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


def _normalizer_db_path_for(db_path: str) -> str:
    path = Path(db_path)
    if path.parent and str(path.parent) != ".":
        return str(path.parent / "learning_normalizer.db")
    return "storage/learning_normalizer.db"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def normalize_term(value: str) -> str:
    """Normalize a slang term for lookup and merging."""
    return normalize_key(value, "slang")


def _slang_fuzzy_allowed(left_key: str, right_key: str) -> bool:
    """Guard aggressive fuzzy merging for short slang tokens.

    Short Chinese slang terms often differ by one character while meaning
    something different. Exact/fingerprint matches are handled before fuzzy
    scoring, so this guard only decides whether rapidfuzz-style similarity is
    safe enough to use.
    """
    if not left_key or not right_key:
        return False
    if min(len(left_key), len(right_key)) <= 3:
        return False
    return not (left_key.isascii() and right_key.isascii() and min(len(left_key), len(right_key)) <= 4)


def _ngram_similarity(left: str, right: str) -> float:
    return NgramSimilarityProvider().similarity(left, right)


def _json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        key = normalize_term(item)
        if not item or not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _normalization_summary(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(meta, dict):
        return None
    cluster_id = str(meta.get("normalization_cluster_id") or "").strip()
    if not cluster_id:
        return None
    return {
        "cluster_id": cluster_id,
        "item_id": str(meta.get("normalization_item_id") or ""),
        "canonical_text": str(meta.get("normalized_from") or ""),
        "normalized_key": str(meta.get("normalized_key") or ""),
        "method": str(meta.get("normalization_method") or ""),
        "score": float(meta.get("normalization_score") or 0.0),
        "auto_merged": bool(meta.get("auto_merged")),
        "features": meta.get("normalization_features") if isinstance(meta.get("normalization_features"), dict) else {},
    }


def _normalized_term_keys(term: str, aliases: list[str] | None = None) -> set[str]:
    keys = {normalize_term(term)}
    for alias in aliases or []:
        keys.add(normalize_term(alias))
    return {key for key in keys if key}


def _matches_any_key(term: str, aliases: list[str] | None, keys: set[str]) -> bool:
    if not keys:
        return False
    return bool(_normalized_term_keys(term, aliases) & keys)


def _merge_alias_values(existing_term: str, existing_aliases: list[str], term: str, aliases: list[str]) -> list[str]:
    extra_terms: list[str] = []
    existing_key = normalize_term(existing_term)
    if normalize_term(term) and normalize_term(term) != existing_key:
        extra_terms.append(term)
    return [
        item
        for item in _dedupe([*existing_aliases, *extra_terms, *aliases])
        if normalize_term(item) != existing_key
    ]


def _merge_aliases_for_existing(existing: SlangTerm, term: str, aliases: list[str]) -> list[str]:
    return _merge_alias_values(existing.term, existing.aliases, term, aliases)


def _row_to_term(row: aiosqlite.Row) -> SlangTerm:
    aliases = _json_loads(row["aliases_json"], [])
    unique_users = _json_loads(row["unique_users_json"], [])
    meta = _json_loads(row["meta_json"], {})
    return SlangTerm(
        term_id=row["term_id"],
        term=row["term"],
        meaning=row["meaning"],
        aliases=[str(item) for item in aliases if str(item).strip()],
        scope=row["scope"],
        group_id=row["group_id"],
        confidence=float(row["confidence"] or 0.0),
        status=row["status"],
        usage_count=int(row["usage_count"] or 0),
        unique_users=[str(item) for item in unique_users if str(item).strip()],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        last_inferred_at=row["last_inferred_at"],
        source=row["source"],
        repeat_policy=row["repeat_policy"],
        notes=row["notes"],
        meta=meta if isinstance(meta, dict) else {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_observation(row: aiosqlite.Row) -> SlangObservation:
    return SlangObservation(
        observation_id=row["observation_id"],
        term_id=row["term_id"],
        group_id=row["group_id"],
        user_id=row["user_id"],
        message_id=row["message_id"],
        raw_text=row["raw_text"],
        context=row["context"],
        observed_at=row["observed_at"],
        reason=row["reason"],
    )


def _row_to_pending(row: aiosqlite.Row) -> SlangPendingCandidate:
    aliases = _json_loads(row["aliases_json"], [])
    unique_users = _json_loads(row["unique_users_json"], [])
    meta = _json_loads(row["meta_json"], {})
    return SlangPendingCandidate(
        pending_id=row["pending_id"],
        term=row["term"],
        meaning=row["meaning"],
        aliases=[str(item) for item in aliases if str(item).strip()],
        group_id=row["group_id"],
        confidence=float(row["confidence"] or 0.0),
        count=int(row["count"] or 0),
        unique_users=[str(item) for item in unique_users if str(item).strip()],
        evidence=row["evidence"],
        reason=row["reason"],
        repeat_policy=row["repeat_policy"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        meta=meta if isinstance(meta, dict) else {},
    )


def _row_to_run(row: aiosqlite.Row) -> SlangExtractionRun:
    meta = _json_loads(row["meta_json"], {})
    return SlangExtractionRun(
        run_id=row["run_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        group_count=int(row["group_count"] or 0),
        scanned_messages=int(row["scanned_messages"] or 0),
        extracted_terms=int(row["extracted_terms"] or 0),
        promoted_candidates=int(row["promoted_candidates"] or 0),
        error=row["error"],
        duration_ms=int(row["duration_ms"] or 0),
        meta=meta if isinstance(meta, dict) else {},
    )


def _term_revision_snapshot(term: SlangTerm | None) -> dict[str, Any]:
    if term is None:
        return {}
    return {
        "term_id": term.term_id,
        "term": term.term,
        "meaning": term.meaning,
        "aliases": term.aliases,
        "scope": term.scope,
        "group_id": term.group_id,
        "confidence": term.confidence,
        "status": term.status,
        "source": term.source,
        "repeat_policy": term.repeat_policy,
        "notes": term.notes,
        "meta": term.meta,
        "normalization": _normalization_summary(term.meta),
    }


def _row_to_revision(row: aiosqlite.Row) -> SlangTermRevision:
    before = _json_loads(row["before_json"], {})
    after = _json_loads(row["after_json"], {})
    meta = _json_loads(row["meta_json"], {})
    return SlangTermRevision(
        revision_id=row["revision_id"],
        term_id=row["term_id"],
        action=row["action"],
        actor=row["actor"],
        before=before if isinstance(before, dict) else {},
        after=after if isinstance(after, dict) else {},
        reason=row["reason"],
        created_at=row["created_at"],
        meta=meta if isinstance(meta, dict) else {},
    )


def _row_to_drift(row: aiosqlite.Row) -> SlangDriftReview:
    aliases = _json_loads(row["aliases_json"], [])
    meta = _json_loads(row["meta_json"], {})
    return SlangDriftReview(
        drift_id=row["drift_id"],
        term_id=row["term_id"],
        term=row["term"],
        group_id=row["group_id"],
        old_meaning=row["old_meaning"],
        new_meaning=row["new_meaning"],
        aliases=[str(item) for item in aliases if str(item).strip()],
        evidence=row["evidence"],
        confidence=float(row["confidence"] or 0.0),
        reason=row["reason"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        meta=meta if isinstance(meta, dict) else {},
    )


def _term_stats_dict(term: SlangTerm) -> dict[str, Any]:
    return {
        "term_id": term.term_id,
        "term": term.term,
        "meaning": term.meaning,
        "scope": term.scope,
        "group_id": term.group_id,
        "status": term.status,
        "confidence": term.confidence,
        "usage_count": term.usage_count,
        "unique_user_count": term.unique_user_count,
        "last_seen_at": term.last_seen_at,
    }


def _settings_cache_key(db_path: str) -> str:
    return db_path


def _term_snapshot_cache_key(db_path: str, group_id: str) -> tuple[str, str]:
    return db_path, group_id


def _clear_term_snapshot_cache(db_path: str) -> None:
    keys = [key for key in _TERM_SNAPSHOT_CACHE if key[0] == db_path]
    for key in keys:
        _TERM_SNAPSHOT_CACHE.pop(key, None)


def _ai_review_sql_condition() -> str:
    return (
        "(source = 'ai_auto_review' "
        "OR meta_json LIKE '%\"ai_approved\": true%' "
        "OR meta_json LIKE '%\"ai_approved\":true%')"
    )


def _human_reviewed_sql_condition() -> str:
    return (
        "(meta_json LIKE '%\"human_reviewed\": true%' "
        "OR meta_json LIKE '%\"human_reviewed\":true%')"
    )


def _candidate_reviewed_sql_condition() -> str:
    failed = _candidate_review_failed_sql_condition()
    return (
        "((meta_json LIKE '%\"candidate_reviewed\": true%' "
        "OR meta_json LIKE '%\"candidate_reviewed\":true%') "
        f"AND NOT {failed})"
    )


def _candidate_review_approved_sql_condition() -> str:
    return (
        "(meta_json LIKE '%\"candidate_review_approved\": true%' "
        "OR meta_json LIKE '%\"candidate_review_approved\":true%')"
    )


def _candidate_review_failed_sql_condition() -> str:
    return (
        "(meta_json LIKE '%\"candidate_review_failed\": true%' "
        "OR meta_json LIKE '%\"candidate_review_failed\":true%' "
        "OR meta_json LIKE '%\"candidate_review_state\": \"failed\"%' "
        "OR meta_json LIKE '%\"candidate_review_state\":\"failed\"%')"
    )


def _ai_rejected_sql_condition() -> str:
    return (
        "(meta_json LIKE '%\"ai_rejected\": true%' "
        "OR meta_json LIKE '%\"ai_rejected\":true%' "
        "OR meta_json LIKE '%\"candidate_review_state\": \"rejected\"%' "
        "OR meta_json LIKE '%\"candidate_review_state\":\"rejected\"%')"
    )


def _is_ai_rejected_muted_term(term: SlangTerm | None) -> bool:
    if term is None or term.status != "muted":
        return False
    meta = term.meta or {}
    if meta.get("human_reviewed") is True or str(meta.get("reviewed_by") or "").strip():
        return False
    return bool(meta.get("ai_rejected") is True or meta.get("candidate_review_state") == "rejected")


def _meta_string_list(meta: dict[str, Any], key: str) -> list[str]:
    value = meta.get(key)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _clear_ai_reject_review_meta(meta: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(meta)
    for key in (
        "ai_rejected",
        "candidate_reviewed",
        "candidate_reviewed_at",
        "candidate_review_complete",
        "candidate_review_decision",
        "candidate_review_decision_confidence",
        "candidate_review_confidence",
        "candidate_review_approved",
        "candidate_review_is_public_meme",
        "candidate_review_reason",
        "candidate_review_state",
        "ai_reviewed_at",
        "ai_reason",
        "reviewed_at",
        "reviewed_by",
    ):
        cleaned.pop(key, None)
    if cleaned.get("review_decision") == "denied":
        cleaned.pop("review_decision", None)
    return cleaned


def _candidate_observe_sql_condition() -> str:
    approved = _candidate_review_approved_sql_condition()
    observe_state = (
        "(meta_json LIKE '%\"review_decision\": \"observe_more\"%' "
        "OR meta_json LIKE '%\"review_decision\":\"observe_more\"%' "
        "OR meta_json LIKE '%\"candidate_review_state\": \"observing\"%' "
        "OR meta_json LIKE '%\"candidate_review_state\":\"observing\"%')"
    )
    legacy_kept = (
        "(meta_json LIKE '%\"candidate_review_state\": \"kept\"%' "
        "OR meta_json LIKE '%\"candidate_review_state\":\"kept\"%')"
    )
    return (
        f"({observe_state} OR ({legacy_kept} AND NOT {approved}))"
    )


def _candidate_review_state_sql_condition(state: str) -> str:
    escaped = state.replace("'", "''")
    return (
        f"(meta_json LIKE '%\"candidate_review_state\": \"{escaped}\"%' "
        f"OR meta_json LIKE '%\"candidate_review_state\":\"{escaped}\"%')"
    )


class SlangStore:
    """Manage slang terms, observations, and runtime settings."""

    def __init__(self, db_path: str | Path = "storage/slang.db") -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._drift_reviewer: Any = None

    def set_drift_reviewer(self, reviewer: Any | None) -> None:
        """Attach the optional semantic drift gate used before opening drift reviews."""
        self._drift_reviewer = reviewer

    async def init(self) -> None:
        db_path = Path(self._db_path)
        existed_before = db_path.exists() and db_path.stat().st_size > 0
        try:
            self._db = await connect_sqlite(self._db_path)
            await self._db.execute("PRAGMA journal_mode=DELETE")
            await self._db.execute("PRAGMA synchronous=FULL")
            if existed_before:
                await self.quick_check()
            await self._db.execute(_CREATE_TERMS)
            await self._db.execute(_CREATE_OBSERVATIONS)
            await self._db.execute(_CREATE_SETTINGS)
            await self._db.execute(_CREATE_PENDING)
            await self._db.execute(_CREATE_PENDING_KEYS)
            await self._db.execute(_CREATE_RUNS)
            await self._db.execute(_CREATE_REVISIONS)
            await self._db.execute(_CREATE_DRIFT_REVIEWS)
            for statement in _INDEXES:
                await self._db.execute(statement)
            await self.rebuild_pending_key_index()
            await self.backfill_ai_rejected_reobserve_meta()
            await self._db.commit()
            await self.quick_check()
        except SlangDatabaseCorruptError:
            await self.close()
            raise
        except sqlite3.DatabaseError as exc:
            await self.close()
            raise SlangDatabaseCorruptError(self._db_path, str(exc)) from exc
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def initialized(self) -> bool:
        return self._db is not None

    @property
    def db_path(self) -> str:
        return self._db_path

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SlangStore is not initialized")
        return self._db

    async def quick_check(self) -> str:
        db = self._require_db()
        try:
            cursor = await db.execute("PRAGMA quick_check")
            rows = await cursor.fetchall()
        except sqlite3.DatabaseError as exc:
            raise SlangDatabaseCorruptError(self._db_path, str(exc)) from exc
        messages = [str(row[0]).strip() for row in rows if row and row[0] is not None]
        if not messages:
            return "ok"
        if all(message.lower() == "ok" for message in messages):
            return "ok"
        raise SlangDatabaseCorruptError(self._db_path, "; ".join(messages[:5]))

    async def _invalidate_term_snapshot_cache(self) -> None:
        _clear_term_snapshot_cache(self._db_path)

    async def _upsert_pending_key_index(
        self,
        pending_id: str,
        *,
        term: str,
        aliases: list[str] | None,
        group_id: str,
    ) -> None:
        db = self._require_db()
        await db.execute("DELETE FROM slang_pending_candidate_keys WHERE pending_id = ?", (pending_id,))
        term_key = normalize_term(term)
        rows: list[tuple[str, str, str, str]] = []
        if term_key:
            rows.append((pending_id, str(group_id), term_key, "term"))
        for alias in aliases or []:
            alias_key = normalize_term(alias)
            if alias_key and alias_key != term_key:
                rows.append((pending_id, str(group_id), alias_key, "alias"))
        if rows:
            await db.executemany(
                """INSERT OR IGNORE INTO slang_pending_candidate_keys
                   (pending_id, group_id, term_key, key_kind)
                   VALUES (?, ?, ?, ?)""",
                rows,
            )

    async def _attach_normalizer_candidate(
        self,
        *,
        domain: str,
        scope: str,
        group_id: str,
        raw_text: str,
        source_table: str,
        source_id: str,
        message_id: int | None = None,
        user_id: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalizer: LearningNormalizerStore | None = None
        try:
            normalizer = LearningNormalizerStore(_normalizer_db_path_for(self._db_path))
            await normalizer.init()
            result = await normalizer.attach_candidate(
                domain=domain,  # type: ignore[arg-type]
                scope=scope,  # type: ignore[arg-type]
                group_id=group_id,
                raw_text=raw_text,
                source_table=source_table,
                source_id=source_id,
                message_id=message_id,
                user_id=user_id,
                profile="slang",
                meta=meta or {},
            )
            return result.to_meta()
        except Exception as exc:
            return {"normalization_error": str(exc)}
        finally:
            if normalizer is not None:
                with contextlib.suppress(Exception):
                    await normalizer.close()

    async def _delete_pending_candidate(self, pending_id: str) -> None:
        db = self._require_db()
        await db.execute("DELETE FROM slang_pending_candidate_keys WHERE pending_id = ?", (pending_id,))
        await db.execute("DELETE FROM slang_pending_candidates WHERE pending_id = ?", (pending_id,))

    async def rebuild_pending_key_index(self) -> None:
        db = self._require_db()
        await db.execute("DELETE FROM slang_pending_candidate_keys")
        cursor = await db.execute("SELECT * FROM slang_pending_candidates")
        rows = await cursor.fetchall()
        for row in rows:
            pending = _row_to_pending(row)
            await self._upsert_pending_key_index(
                pending.pending_id,
                term=pending.term,
                aliases=pending.aliases,
                group_id=pending.group_id,
            )

    def _get_cached_settings(self) -> SlangSettings | None:
        cache = _SETTINGS_CACHE.get(_settings_cache_key(self._db_path))
        if cache is None:
            return None
        if time.monotonic() - cache.cached_at > _CACHE_TTL_S:
            _SETTINGS_CACHE.pop(_settings_cache_key(self._db_path), None)
            return None
        return cache.settings

    def _set_cached_settings(self, settings: SlangSettings) -> None:
        _SETTINGS_CACHE[_settings_cache_key(self._db_path)] = _SettingsCacheEntry(
            settings=settings,
            cached_at=time.monotonic(),
        )

    def _get_cached_term_snapshot(self, group_id: str) -> list[SlangTerm] | None:
        cache = _TERM_SNAPSHOT_CACHE.get(_term_snapshot_cache_key(self._db_path, group_id))
        if cache is None:
            return None
        if time.monotonic() - cache.cached_at > _CACHE_TTL_S:
            _TERM_SNAPSHOT_CACHE.pop(_term_snapshot_cache_key(self._db_path, group_id), None)
            return None
        return cache.terms

    def _set_cached_term_snapshot(self, group_id: str, terms: list[SlangTerm]) -> None:
        _TERM_SNAPSHOT_CACHE[_term_snapshot_cache_key(self._db_path, group_id)] = _TermSnapshotCacheEntry(
            terms=terms,
            cached_at=time.monotonic(),
        )

    async def record_revision(
        self,
        term_id: str,
        *,
        action: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        actor: str = "system",
        reason: str = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        """Record a governance change for later audit in Admin Web."""
        db = self._require_db()
        revision_id = _new_id("rev")
        await db.execute(
            """INSERT INTO slang_term_revisions
               (revision_id, term_id, action, actor, before_json, after_json, reason, created_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                revision_id,
                term_id,
                str(action or "update"),
                str(actor or "system"),
                json.dumps(before or {}, ensure_ascii=False),
                json.dumps(after or {}, ensure_ascii=False),
                str(reason or "")[:800],
                _now_iso(),
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        await db.commit()
        return revision_id

    async def list_revisions(self, term_id: str, *, limit: int = 50) -> list[SlangTermRevision]:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM slang_term_revisions
               WHERE term_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (term_id, max(1, min(int(limit or 50), 200))),
        )
        return [_row_to_revision(row) for row in await cursor.fetchall()]

    async def _maybe_create_drift_review(
        self,
        existing: SlangTerm,
        *,
        new_meaning: str,
        aliases: list[str],
        evidence: str,
        confidence: float,
        reason: str,
        meta: dict[str, Any] | None = None,
        settings: SlangSettings | None = None,
    ) -> _DriftReviewOutcome:
        settings = settings or await self.load_settings()
        if not settings.drift_detection_enabled:
            return _DriftReviewOutcome()
        if existing.status != "approved" or confidence < settings.drift_min_confidence:
            return _DriftReviewOutcome()
        new_meaning = str(new_meaning or "").strip()
        if not new_meaning or not existing.meaning.strip():
            return _DriftReviewOutcome()
        similarity = _ngram_similarity(existing.meaning, new_meaning)
        drift_meta = {
            **(meta or {}),
            "meaning_similarity": round(similarity, 3),
            "source_status": existing.status,
        }
        if similarity >= _DRIFT_LEXICAL_SAME_THRESHOLD:
            drift_meta.update({
                "drift_semantic_reviewed": False,
                "drift_semantic_verdict": "same_meaning",
                "drift_semantic_confidence": round(similarity, 3),
                "drift_semantic_reason": "lexical_similarity_guard",
            })
            return _DriftReviewOutcome(
                verdict="same_meaning",
                confidence=similarity,
                reason="lexical_similarity_guard",
                meaning_similarity=similarity,
                meta=drift_meta,
            )

        reviewer = self._drift_reviewer
        if reviewer is None or not hasattr(reviewer, "review_drift"):
            drift_meta.update({
                "drift_semantic_reviewed": False,
                "drift_semantic_verdict": "unclear",
                "drift_semantic_reason": "drift_reviewer_unavailable",
            })
            return _DriftReviewOutcome(
                verdict="unclear",
                reason="drift_reviewer_unavailable",
                meaning_similarity=similarity,
                meta=drift_meta,
            )
        try:
            assessment = await reviewer.review_drift(
                existing=existing,
                new_meaning=new_meaning,
                aliases=aliases,
                evidence=evidence,
                confidence=confidence,
                reason=reason,
            )
        except Exception as exc:
            assessment = SlangDriftAssessment(
                verdict="unclear",
                reviewed=True,
                error=f"drift_reviewer_failed:{exc}",
            )
        drift_meta.update(assessment.to_meta())
        if assessment.verdict != "real_drift":
            if assessment.reviewed and not assessment.error:
                await self.record_revision(
                    existing.term_id,
                    action="drift_alias_candidate" if assessment.verdict == "alias_candidate" else "drift_suppressed",
                    actor="ai",
                    before=_term_revision_snapshot(existing),
                    after=_term_revision_snapshot(existing),
                    reason=assessment.reason or reason or "semantic drift gate suppressed",
                    meta={
                        **drift_meta,
                        "new_meaning": new_meaning,
                        "candidate_aliases": aliases,
                    },
                )
            return _DriftReviewOutcome(
                verdict=assessment.verdict,
                confidence=assessment.confidence,
                reason=assessment.reason or assessment.error,
                meaning_similarity=similarity,
                meta=drift_meta,
            )
        db = self._require_db()
        cursor = await db.execute(
            """SELECT drift_id FROM slang_drift_reviews
               WHERE term_id = ? AND status = 'open'
               ORDER BY updated_at DESC
               LIMIT 1""",
            (existing.term_id,),
        )
        row = await cursor.fetchone()
        now = _now_iso()
        if row is not None:
            drift_id = row["drift_id"]
            await db.execute(
                """UPDATE slang_drift_reviews
                   SET new_meaning = ?, aliases_json = ?, evidence = ?, confidence = ?,
                       reason = ?, updated_at = ?, meta_json = ?
                   WHERE drift_id = ?""",
                (
                    new_meaning,
                    json.dumps(_dedupe([*existing.aliases, *aliases]), ensure_ascii=False),
                    str(evidence or "")[:2000],
                    max(0.0, min(1.0, confidence)),
                    str(reason or "")[:800],
                    now,
                    json.dumps(drift_meta, ensure_ascii=False),
                    drift_id,
                ),
            )
            await db.commit()
            return _DriftReviewOutcome(
                drift_id=drift_id,
                verdict=assessment.verdict,
                confidence=assessment.confidence,
                reason=assessment.reason,
                meaning_similarity=similarity,
                meta=drift_meta,
            )
        drift_id = _new_id("drift")
        await db.execute(
            """INSERT INTO slang_drift_reviews
               (drift_id, term_id, term_key, term, group_id, old_meaning, new_meaning,
                aliases_json, evidence, confidence, reason, status, created_at, updated_at, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (
                drift_id,
                existing.term_id,
                normalize_term(existing.term),
                existing.term,
                existing.group_id,
                existing.meaning,
                new_meaning,
                json.dumps(_dedupe([*existing.aliases, *aliases]), ensure_ascii=False),
                str(evidence or "")[:2000],
                max(0.0, min(1.0, confidence)),
                str(reason or "")[:800],
                now,
                now,
                json.dumps(drift_meta, ensure_ascii=False),
            ),
        )
        await db.commit()
        await self.record_revision(
            existing.term_id,
            action="drift_detected",
            before=_term_revision_snapshot(existing),
            after={"drift_id": drift_id, "new_meaning": new_meaning, "confidence": confidence},
            reason=reason or "detected conflicting meaning",
            meta=drift_meta,
        )
        return _DriftReviewOutcome(
            drift_id=drift_id,
            verdict=assessment.verdict,
            confidence=assessment.confidence,
            reason=assessment.reason,
            meaning_similarity=similarity,
            meta=drift_meta,
        )

    async def load_settings(self) -> SlangSettings:
        cached = self._get_cached_settings()
        if cached is not None:
            return cached
        db = self._require_db()
        cursor = await db.execute("SELECT value_json FROM slang_settings WHERE key = 'settings'")
        row = await cursor.fetchone()
        if row is None:
            settings = SlangSettings()
            await self.save_settings(settings)
            return settings
        data = _json_loads(row["value_json"], {})
        try:
            settings = SlangSettings.model_validate(data)
        except Exception:
            settings = SlangSettings()
        self._set_cached_settings(settings)
        return settings

    async def save_settings(self, settings: SlangSettings | dict[str, Any]) -> SlangSettings:
        db = self._require_db()
        model = settings if isinstance(settings, SlangSettings) else SlangSettings.model_validate(settings)
        now = _now_iso()
        await db.execute(
            """INSERT INTO slang_settings (key, value_json, updated_at)
               VALUES ('settings', ?, ?)
               ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
            (model.model_dump_json(), now),
        )
        await db.commit()
        self._set_cached_settings(model)
        return model

    async def is_stoplisted(
        self,
        term: str,
        settings: SlangSettings | None = None,
        aliases: list[str] | None = None,
    ) -> bool:
        settings = settings or await self.load_settings()
        candidate_keys = _normalized_term_keys(term, aliases)
        if not candidate_keys:
            return True
        stop_keys = {normalize_term(item) for item in settings.stoplist}
        stop_keys = {key for key in stop_keys if key}
        return bool(candidate_keys & stop_keys)

    async def is_muted_term(self, *, term: str, group_id: str) -> bool:
        existing = await self.find_existing(term=term, group_id=group_id, scope="group")
        if existing and existing.status == "muted":
            return True
        global_existing = await self.find_existing(term=term, group_id="", scope="global")
        return bool(global_existing and global_existing.status == "muted")

    async def _ai_reject_reobserve_base(
        self,
        term: SlangTerm,
        *,
        extra_user_id: str = "",
    ) -> tuple[int, list[str]]:
        """Return lightweight evidence counts for an AI-rejected muted term."""
        db = self._require_db()
        cursor = await db.execute(
            """SELECT COUNT(*) AS cnt FROM slang_observations
               WHERE term_id = ? AND group_id = ?""",
            (term.term_id, term.group_id),
        )
        observation_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            """SELECT DISTINCT user_id FROM slang_observations
               WHERE term_id = ? AND group_id = ? AND user_id != ''""",
            (term.term_id, term.group_id),
        )
        observation_users = [str(row["user_id"]) for row in await cursor.fetchall() if str(row["user_id"]).strip()]
        users = _dedupe([
            *term.unique_users,
            *_meta_string_list(term.meta, "rejected_reobserve_users"),
            *observation_users,
            str(extra_user_id or ""),
        ])
        count = max(
            int(term.usage_count or 0),
            observation_count,
            int(term.meta.get("rejected_reobserve_count") or 0),
        )
        return count, users

    async def _record_ai_rejected_reobservation(
        self,
        term: SlangTerm,
        *,
        group_id: str,
        user_id: str,
        message_id: int | None,
        raw_text: str,
        context: str,
        observed_count: int,
    ) -> str | None:
        if not _is_ai_rejected_muted_term(term) or str(term.group_id) != str(group_id):
            return term.term_id
        meta = dict(term.meta)
        message_key = str(message_id) if message_id is not None else ""
        message_ids = _meta_string_list(meta, "rejected_reobserve_message_ids")
        if message_key and message_key in set(message_ids):
            return term.term_id

        base_count, users = await self._ai_reject_reobserve_base(term, extra_user_id=user_id)
        increment = max(1, int(observed_count or 1))
        next_count = base_count + increment
        now = _now_iso()
        if message_key:
            message_ids = [*message_ids, message_key][-_AI_REJECT_REOBSERVE_MAX_TRACKED_MESSAGES:]
        evidence = (raw_text or context or meta.get("rejected_reobserve_evidence") or meta.get("evidence") or "")
        next_meta = {
            **meta,
            "rejected_reobserve_count": next_count,
            "rejected_reobserve_users": users,
            "rejected_reobserve_user_count": len(users),
            "last_rejected_reobserve_at": now,
            "rejected_reobserve_evidence": str(evidence)[:_AI_REJECT_REOBSERVE_MAX_EVIDENCE_CHARS],
            "rejected_reobserve_message_ids": message_ids,
            "rejected_reobserve_threshold_count": _AI_REJECT_REOBSERVE_COUNT_THRESHOLD,
            "rejected_reobserve_threshold_users": _AI_REJECT_REOBSERVE_USER_THRESHOLD,
        }
        should_revive = (
            next_count >= _AI_REJECT_REOBSERVE_COUNT_THRESHOLD
            or len(users) >= _AI_REJECT_REOBSERVE_USER_THRESHOLD
        )
        if should_revive:
            next_meta = _clear_ai_reject_review_meta(next_meta)
            next_meta.update({
                "revived_from_ai_reject": True,
                "revived_at": now,
                "revival_reason": "reobserved_after_ai_reject",
            })
            await self.update_term(
                term.term_id,
                status="candidate",
                source="ai_reject_reobserved",
                meta=next_meta,
                last_inferred_at=now,
                revision_action="ai_reject_reobserve_revival",
                revision_actor="system",
                revision_reason="AI-rejected slang candidate reobserved after mute",
                revision_meta={
                    "rejected_reobserve_count": next_count,
                    "rejected_reobserve_user_count": len(users),
                    "threshold_count": _AI_REJECT_REOBSERVE_COUNT_THRESHOLD,
                    "threshold_users": _AI_REJECT_REOBSERVE_USER_THRESHOLD,
                },
            )
        else:
            await self.update_term(
                term.term_id,
                meta=next_meta,
                last_inferred_at=now,
                revision_action="ai_reject_reobserve",
                revision_actor="system",
                revision_reason="AI-rejected slang candidate reobserved below threshold",
                revision_meta={
                    "rejected_reobserve_count": next_count,
                    "rejected_reobserve_user_count": len(users),
                },
            )
        return term.term_id

    async def backfill_ai_rejected_reobserve_meta(self) -> dict[str, int]:
        db = self._require_db()
        cursor = await db.execute(
            f"""SELECT * FROM slang_terms
                WHERE status = 'muted' AND {_ai_rejected_sql_condition()}"""
        )
        checked = updated = revived = 0
        now = _now_iso()
        for row in await cursor.fetchall():
            term = _row_to_term(row)
            if not _is_ai_rejected_muted_term(term):
                continue
            if int(term.meta.get("rejected_reobserve_backfill_version") or 0) >= _AI_REJECT_REOBSERVE_BACKFILL_VERSION:
                continue
            checked += 1
            count, users = await self._ai_reject_reobserve_base(term)
            latest_cursor = await db.execute(
                """SELECT raw_text, context FROM slang_observations
                   WHERE term_id = ? AND group_id = ?
                   ORDER BY observed_at DESC LIMIT 1""",
                (term.term_id, term.group_id),
            )
            latest = await latest_cursor.fetchone()
            evidence = ""
            if latest is not None:
                evidence = str(latest["raw_text"] or latest["context"] or "")
            evidence = evidence or str(term.meta.get("rejected_reobserve_evidence") or term.meta.get("evidence") or "")
            next_meta = {
                **term.meta,
                "rejected_reobserve_count": count,
                "rejected_reobserve_users": users,
                "rejected_reobserve_user_count": len(users),
                "rejected_reobserve_evidence": evidence[:_AI_REJECT_REOBSERVE_MAX_EVIDENCE_CHARS],
                "rejected_reobserve_threshold_count": _AI_REJECT_REOBSERVE_COUNT_THRESHOLD,
                "rejected_reobserve_threshold_users": _AI_REJECT_REOBSERVE_USER_THRESHOLD,
                "rejected_reobserve_backfill_version": _AI_REJECT_REOBSERVE_BACKFILL_VERSION,
                "rejected_reobserve_backfilled_at": now,
            }
            should_revive = (
                count >= _AI_REJECT_REOBSERVE_COUNT_THRESHOLD
                or len(users) >= _AI_REJECT_REOBSERVE_USER_THRESHOLD
            )
            if should_revive:
                next_meta = _clear_ai_reject_review_meta(next_meta)
                next_meta.update({
                    "revived_from_ai_reject": True,
                    "revived_at": now,
                    "revival_reason": "reobserved_after_ai_reject",
                })
                if await self.update_term(
                    term.term_id,
                    status="candidate",
                    source="ai_reject_reobserved",
                    meta=next_meta,
                    last_inferred_at=now,
                    revision_action="ai_reject_reobserve_backfill_revival",
                    revision_actor="system",
                    revision_reason="AI-rejected slang candidate revived from backfilled observations",
                    revision_meta={
                        "rejected_reobserve_count": count,
                        "rejected_reobserve_user_count": len(users),
                    },
                ):
                    revived += 1
                    updated += 1
            elif await self.update_term(
                term.term_id,
                meta=next_meta,
                revision_action="ai_reject_reobserve_backfill",
                revision_actor="system",
                revision_reason="AI-rejected slang candidate reobserve counters backfilled",
                revision_meta={
                    "rejected_reobserve_count": count,
                    "rejected_reobserve_user_count": len(users),
                },
            ):
                updated += 1
        return {"checked": checked, "updated": updated, "revived": revived}

    async def get_meta(self, key: str, default: Any = None) -> Any:
        db = self._require_db()
        cursor = await db.execute("SELECT value_json FROM slang_settings WHERE key = ?", (f"meta:{key}",))
        row = await cursor.fetchone()
        if row is None:
            return default
        return _json_loads(row["value_json"], default)

    async def set_meta(self, key: str, value: Any) -> None:
        db = self._require_db()
        await db.execute(
            """INSERT INTO slang_settings (key, value_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
            (f"meta:{key}", json.dumps(value, ensure_ascii=False), _now_iso()),
        )
        await db.commit()

    async def find_existing(
        self,
        *,
        term: str,
        group_id: str,
        scope: SlangScope = "group",
        aliases: list[str] | None = None,
    ) -> SlangTerm | None:
        candidate_keys = _normalized_term_keys(term, aliases)
        if not candidate_keys:
            return None
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM slang_terms
               WHERE scope = ? AND group_id = ?
               ORDER BY
                 CASE status WHEN 'approved' THEN 0 WHEN 'candidate' THEN 1 WHEN 'muted' THEN 2 ELSE 3 END,
                 confidence DESC,
                 created_at ASC""",
            (scope, "" if scope == "global" else group_id),
        )
        for candidate in await cursor.fetchall():
            term_obj = _row_to_term(candidate)
            existing_keys = _normalized_term_keys(term_obj.term, term_obj.aliases)
            if candidate_keys & existing_keys:
                return term_obj
        return None

    async def find_similar_existing(
        self,
        *,
        term: str,
        group_id: str,
        scope: SlangScope = "group",
        min_score: float = 0.92,
    ) -> SlangTerm | None:
        key = normalize_term(term)
        if not key:
            return None
        if not _slang_fuzzy_allowed(key, key):
            return None
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM slang_terms
               WHERE scope = ? AND group_id = ?
                 AND status IN ('approved', 'candidate')
               ORDER BY
                 CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                 confidence DESC,
                 updated_at DESC
               LIMIT 300""",
            (scope, "" if scope == "global" else group_id),
        )
        best: tuple[SlangTerm, float] | None = None
        for row in await cursor.fetchall():
            candidate = _row_to_term(row)
            values = [candidate.term, *candidate.aliases]
            scores: list[float] = []
            for value in values:
                value_key = normalize_term(value)
                if not _slang_fuzzy_allowed(key, value_key):
                    continue
                scores.append(score_similarity(term, value, "slang").score)
            score = max(scores) if scores else 0.0
            if score >= min_score and (best is None or score > best[1]):
                best = (candidate, score)
        return best[0] if best else None

    async def _upsert_pending_candidate(
        self,
        *,
        term: str,
        meaning: str,
        aliases: list[str],
        group_id: str,
        user_id: str,
        confidence: float,
        evidence: str,
        reason: str,
        repeat_policy: RepeatPolicy,
        observed_count: int,
        min_count: int,
        meta: dict[str, Any] | None,
    ) -> str | None:
        key = normalize_term(term)
        now = _now_iso()
        db = self._require_db()
        cursor = await db.execute(
            "SELECT * FROM slang_pending_candidates WHERE term_key = ? AND group_id = ?",
            (key, group_id),
        )
        row = await cursor.fetchone()
        if row is None:
            pending_id = _new_id("pending")
            merged_meta = {
                **(meta or {}),
                **await self._attach_normalizer_candidate(
                    domain="slang",
                    scope="group",
                    group_id=group_id,
                    raw_text=term,
                    source_table="slang_pending_candidates",
                    source_id=pending_id,
                    user_id=user_id,
                    meta={"path": "pending_candidate"},
                ),
            }
            unique_users = [user_id] if user_id else []
            await db.execute(
                """INSERT INTO slang_pending_candidates
                   (pending_id, term_key, term, meaning, aliases_json, group_id, confidence,
                    count, unique_users_json, evidence, reason, repeat_policy, first_seen_at,
                    last_seen_at, meta_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pending_id,
                    key,
                    term.strip(),
                    meaning.strip(),
                    json.dumps(aliases, ensure_ascii=False),
                    group_id,
                    confidence,
                    max(1, observed_count),
                    json.dumps(unique_users, ensure_ascii=False),
                    evidence[:1000],
                    reason[:500],
                    repeat_policy,
                    now,
                    now,
                    json.dumps(merged_meta, ensure_ascii=False),
                ),
            )
            await self._upsert_pending_key_index(
                pending_id,
                term=term,
                aliases=aliases,
                group_id=group_id,
            )
            await db.commit()
            await self.record_observation(
                pending_id,
                group_id=group_id,
                user_id=user_id,
                raw_text=evidence,
                context=evidence,
                reason=reason or "pending_extraction",
            )
            if max(1, observed_count) >= min_count:
                return await self._promote_pending_candidate(pending_id)
            return None

        pending = _row_to_pending(row)
        merged_aliases = _dedupe([*pending.aliases, *aliases])
        unique_users = set(pending.unique_users)
        if user_id:
            unique_users.add(str(user_id))
        new_count = pending.count + max(1, observed_count)
        merged_meta = {
            **pending.meta,
            **(meta or {}),
            **await self._attach_normalizer_candidate(
                domain="slang",
                scope="group",
                group_id=group_id,
                raw_text=term,
                source_table="slang_pending_candidates",
                source_id=pending.pending_id,
                user_id=user_id,
                meta={"path": "pending_candidate_reinforce"},
            ),
        }
        await db.execute(
            """UPDATE slang_pending_candidates
               SET meaning = ?, aliases_json = ?, confidence = ?, count = ?,
                   unique_users_json = ?, evidence = ?, reason = ?, repeat_policy = ?,
                   last_seen_at = ?, meta_json = ?
               WHERE pending_id = ?""",
            (
                meaning.strip() if meaning and confidence >= pending.confidence else pending.meaning,
                json.dumps(merged_aliases, ensure_ascii=False),
                max(pending.confidence, confidence),
                new_count,
                json.dumps(sorted(unique_users), ensure_ascii=False),
                (evidence or pending.evidence)[:1000],
                (reason or pending.reason)[:500],
                repeat_policy if pending.repeat_policy == "understand_only" else pending.repeat_policy,
                now,
                json.dumps(merged_meta, ensure_ascii=False),
                pending.pending_id,
            ),
        )
        await self._upsert_pending_key_index(
            pending.pending_id,
            term=pending.term,
            aliases=merged_aliases,
            group_id=group_id,
        )
        await db.commit()
        await self.record_observation(
            pending.pending_id,
            group_id=group_id,
            user_id=user_id,
            raw_text=evidence,
            context=evidence,
            reason=reason or "pending_extraction",
        )
        if new_count >= min_count:
            return await self._promote_pending_candidate(pending.pending_id)
        return None

    async def _promote_pending_candidate(self, pending_id: str) -> str | None:
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM slang_pending_candidates WHERE pending_id = ?", (pending_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        pending = _row_to_pending(row)
        existing = await self.find_existing(
            term=pending.term,
            aliases=pending.aliases,
            group_id=pending.group_id,
            scope="group",
        )
        if existing:
            await db.execute(
                "UPDATE slang_observations SET term_id = ? WHERE term_id = ?",
                (existing.term_id, pending.pending_id),
            )
            if existing.status not in {"muted", "expired"}:
                settings = await self.load_settings()
                drift_outcome = await self._maybe_create_drift_review(
                    existing,
                    new_meaning=pending.meaning,
                    aliases=pending.aliases,
                    evidence=pending.evidence,
                    confidence=pending.confidence,
                    reason=pending.reason or "pending_candidate_promote",
                    meta=pending.meta,
                    settings=settings,
                )
                merged_meta = {
                    **existing.meta,
                    **pending.meta,
                    "merged_pending_ids": _dedupe([
                        *(str(item) for item in existing.meta.get("merged_pending_ids", [])),
                        pending.pending_id,
                    ]),
                }
                if drift_outcome.drift_id:
                    merged_meta["last_pending_drift_id"] = drift_outcome.drift_id
                updates: dict[str, Any] = {
                    "confidence": max(existing.confidence, pending.confidence),
                    "last_inferred_at": _now_iso(),
                    "meta": merged_meta,
                    "revision_action": "pending_promote_merge",
                    "revision_reason": pending.reason or "pending candidate merged into existing term",
                    "revision_meta": {
                        "pending_id": pending.pending_id,
                        "drift_id": drift_outcome.drift_id or "",
                        "drift_verdict": drift_outcome.verdict,
                    },
                }
                if drift_outcome.allows_alias_merge:
                    updates["aliases"] = _merge_aliases_for_existing(existing, pending.term, pending.aliases)
                if pending.repeat_policy and existing.repeat_policy == "understand_only":
                    updates["repeat_policy"] = pending.repeat_policy
                if (
                    drift_outcome.allows_meaning_update
                    and pending.meaning
                    and (not existing.meaning or pending.confidence >= existing.confidence)
                ):
                    updates["meaning"] = pending.meaning.strip()
                await self.update_term(existing.term_id, **updates)
                users = sorted({*existing.unique_users, *pending.unique_users})
                now = _now_iso()
                await db.execute(
                    """UPDATE slang_terms
                       SET usage_count = usage_count + ?,
                           unique_users_json = ?,
                           last_seen_at = ?,
                           updated_at = ?
                       WHERE term_id = ?""",
                    (
                        max(1, pending.count),
                        json.dumps(users, ensure_ascii=False),
                        pending.last_seen_at or now,
                        now,
                        existing.term_id,
                    ),
                )
            await self._delete_pending_candidate(pending_id)
            await db.commit()
            await self._invalidate_term_snapshot_cache()
            return existing.term_id
        now = _now_iso()
        term_id = _new_id("slang")
        await db.execute(
            """INSERT INTO slang_terms
               (term_id, term_key, term, meaning, aliases_json, scope, group_id, confidence,
                status, usage_count, unique_users_json, first_seen_at, last_seen_at,
                last_inferred_at, source, repeat_policy, notes, meta_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'group', ?, ?, 'candidate', ?, ?, ?, ?, ?, 'extractor', ?, '', ?, ?, ?)""",
            (
                term_id,
                normalize_term(pending.term),
                pending.term,
                pending.meaning,
                json.dumps(pending.aliases, ensure_ascii=False),
                pending.group_id,
                pending.confidence,
                pending.count,
                json.dumps(pending.unique_users, ensure_ascii=False),
                pending.first_seen_at,
                pending.last_seen_at,
                now,
                pending.repeat_policy,
                json.dumps({**pending.meta, "promoted_from_pending": pending.pending_id}, ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.execute(
            """UPDATE slang_observations
               SET term_id = ?
               WHERE term_id = ?""",
            (term_id, pending.pending_id),
        )
        await self._delete_pending_candidate(pending_id)
        await db.commit()
        await self._invalidate_term_snapshot_cache()
        return term_id

    async def _merge_pending_candidates_into_existing(
        self,
        existing: SlangTerm,
        *,
        term: str,
        aliases: list[str],
        settings: SlangSettings | None = None,
    ) -> None:
        pending_keys = _normalized_term_keys(term, aliases)
        if not pending_keys:
            return
        db = self._require_db()
        sorted_keys = sorted(pending_keys)
        cursor = await db.execute(
            f"""SELECT DISTINCT pending_id
                FROM slang_pending_candidate_keys
                WHERE group_id = ?
                  AND term_key IN ({','.join('?' for _ in sorted_keys)})""",
            (existing.group_id, *sorted_keys),
        )
        pending_ids = [str(row["pending_id"]) for row in await cursor.fetchall()]
        if not pending_ids:
            return
        cursor = await db.execute(
            f"""SELECT * FROM slang_pending_candidates
                WHERE pending_id IN ({','.join('?' for _ in pending_ids)})""",
            pending_ids,
        )
        rows = await cursor.fetchall()
        pending_rows: list[SlangPendingCandidate] = []
        for row in rows:
            pending = _row_to_pending(row)
            if _normalized_term_keys(pending.term, pending.aliases) & pending_keys:
                pending_rows.append(pending)
        if not pending_rows:
            return

        settings = settings or await self.load_settings()
        merged_aliases = list(existing.aliases)
        merged_meta = dict(existing.meta)
        merged_users = set(existing.unique_users)
        total_usage = 0
        last_seen_at = existing.last_seen_at
        latest_inferred_at = existing.last_inferred_at or _now_iso()
        repeat_policy = existing.repeat_policy
        best_meaning = existing.meaning
        best_confidence = existing.confidence
        merged_pending_ids = [str(item) for item in existing.meta.get("merged_pending_ids", [])]
        drift_ids: list[str] = []

        for pending in pending_rows:
            await db.execute(
                "UPDATE slang_observations SET term_id = ? WHERE term_id = ?",
                (existing.term_id, pending.pending_id),
            )
            merged_meta.update(pending.meta)
            merged_pending_ids.append(pending.pending_id)
            merged_users.update(pending.unique_users)
            total_usage += max(1, pending.count)
            last_seen_at = max(last_seen_at, pending.last_seen_at)
            latest_inferred_at = max(latest_inferred_at, pending.last_seen_at)
            if repeat_policy == "understand_only" and pending.repeat_policy in VALID_REPEAT_POLICIES:
                repeat_policy = pending.repeat_policy
            drift_outcome = await self._maybe_create_drift_review(
                existing,
                new_meaning=pending.meaning,
                aliases=pending.aliases,
                evidence=pending.evidence,
                confidence=pending.confidence,
                reason=pending.reason or "pending_candidate_merge",
                meta=pending.meta,
                settings=settings,
            )
            if drift_outcome.allows_alias_merge:
                merged_aliases = _merge_alias_values(existing.term, merged_aliases, pending.term, pending.aliases)
            if drift_outcome.drift_id:
                drift_ids.append(drift_outcome.drift_id)
            elif (
                drift_outcome.allows_meaning_update
                and pending.meaning
                and (not best_meaning or pending.confidence >= best_confidence)
            ):
                best_meaning = pending.meaning.strip()
            best_confidence = max(best_confidence, pending.confidence)
            await self._delete_pending_candidate(pending.pending_id)

        merged_meta["merged_pending_ids"] = _dedupe(merged_pending_ids)
        if drift_ids:
            merged_meta["last_pending_drift_id"] = drift_ids[-1]
        await self.update_term(
            existing.term_id,
            aliases=merged_aliases,
            confidence=best_confidence,
            meaning=best_meaning,
            repeat_policy=repeat_policy,
            meta=merged_meta,
            last_inferred_at=latest_inferred_at,
            revision_action="pending_candidate_merge_existing",
            revision_reason="pending candidates merged into existing term",
            revision_meta={"pending_ids": merged_pending_ids, "drift_ids": drift_ids},
        )
        now = _now_iso()
        await db.execute(
            """UPDATE slang_terms
               SET usage_count = usage_count + ?,
                   unique_users_json = ?,
                   last_seen_at = ?,
                   updated_at = ?
               WHERE term_id = ?""",
            (
                total_usage,
                json.dumps(sorted(merged_users), ensure_ascii=False),
                last_seen_at,
                now,
                existing.term_id,
            ),
        )
        await db.commit()
        await self._invalidate_term_snapshot_cache()

    async def upsert_candidate(
        self,
        *,
        term: str,
        meaning: str = "",
        aliases: list[str] | None = None,
        group_id: str,
        user_id: str = "",
        message_id: int | None = None,
        raw_text: str = "",
        context: str = "",
        confidence: float = 0.3,
        reason: str = "",
        repeat_policy: RepeatPolicy = "understand_only",
        source: str = "extractor",
        meta: dict[str, Any] | None = None,
        min_count: int = 1,
        observed_count: int = 1,
        settings: SlangSettings | None = None,
    ) -> str | None:
        key = normalize_term(term)
        if not key or not group_id:
            return None
        settings = settings or await self.load_settings()
        aliases = _dedupe(aliases or [])
        if await self.is_stoplisted(term, settings, aliases):
            return None
        confidence = max(0.0, min(1.0, float(confidence)))
        candidate_meta = {**(meta or {}), "llm_confidence": confidence}
        if repeat_policy not in VALID_REPEAT_POLICIES:
            repeat_policy = "understand_only"

        existing = await self.find_existing(term=term, aliases=aliases, group_id=group_id, scope="group")
        if existing is None:
            existing = await self.find_similar_existing(term=term, group_id=group_id, scope="group")
            if existing is not None:
                candidate_meta["normalizer_similar_existing"] = True
        if existing and await self.is_stoplisted(existing.term, settings, [*existing.aliases, term, *aliases]):
            return None
        if existing and existing.status in {"muted", "expired"}:
            if _is_ai_rejected_muted_term(existing):
                return await self._record_ai_rejected_reobservation(
                    existing,
                    group_id=str(group_id),
                    user_id=user_id,
                    message_id=message_id,
                    raw_text=raw_text,
                    context=context,
                    observed_count=observed_count,
                )
            return existing.term_id
        global_existing = await self.find_existing(term=term, aliases=aliases, group_id="", scope="global")
        if global_existing and await self.is_stoplisted(
            global_existing.term,
            settings,
            [*global_existing.aliases, term, *aliases],
        ):
            return None
        if global_existing and global_existing.status == "muted":
            return None
        if existing:
            norm_meta = await self._attach_normalizer_candidate(
                domain="slang",
                scope=existing.scope,
                group_id=existing.group_id,
                raw_text=term,
                source_table="slang_terms",
                source_id=existing.term_id,
                message_id=message_id,
                user_id=user_id,
                meta={"path": "existing_term_hit"},
            )
            candidate_meta = {**candidate_meta, **norm_meta}
            drift_outcome = await self._maybe_create_drift_review(
                existing,
                new_meaning=meaning,
                aliases=aliases,
                evidence=raw_text or context,
                confidence=confidence,
                reason=reason,
                meta=candidate_meta,
                settings=settings,
            )
            if drift_outcome.drift_id:
                await self.record_hit(
                    existing.term_id,
                    group_id=group_id,
                    user_id=user_id,
                    message_id=message_id,
                    raw_text=raw_text,
                    context=context,
                    reason=reason or "drift_observation",
                )
                return existing.term_id
            if not drift_outcome.allows_alias_merge and not drift_outcome.allows_meaning_update:
                await self.record_hit(
                    existing.term_id,
                    group_id=group_id,
                    user_id=user_id,
                    message_id=message_id,
                    raw_text=raw_text,
                    context=context,
                    reason=reason or "drift_gate_suppressed",
                )
                return existing.term_id
            updates: dict[str, Any] = {
                "confidence": max(existing.confidence, confidence),
                "last_inferred_at": _now_iso(),
                "revision_action": "candidate_update",
                "revision_reason": reason,
                "revision_meta": {"normalization": _normalization_summary(candidate_meta)},
            }
            merged_meta = {**existing.meta, **candidate_meta}
            updates["meta"] = merged_meta
            if drift_outcome.allows_alias_merge:
                updates["aliases"] = _merge_aliases_for_existing(existing, term, aliases)
            if (
                drift_outcome.allows_meaning_update
                and meaning
                and (not existing.meaning or confidence >= existing.confidence)
            ):
                updates["meaning"] = meaning.strip()
            if repeat_policy and existing.repeat_policy == "understand_only":
                updates["repeat_policy"] = repeat_policy
            await self.update_term(existing.term_id, **updates)
            await self._merge_pending_candidates_into_existing(
                await self.get_term(existing.term_id) or existing,
                term=term,
                aliases=aliases,
                settings=settings,
            )
            await self.record_hit(
                existing.term_id,
                group_id=group_id,
                user_id=user_id,
                message_id=message_id,
                raw_text=raw_text,
                context=context,
                reason=reason,
            )
            return existing.term_id

        min_count = max(1, int(min_count))
        observed_count = max(1, int(observed_count))
        db = self._require_db()
        pending_cursor = await db.execute(
            "SELECT 1 FROM slang_pending_candidates WHERE term_key = ? AND group_id = ? LIMIT 1",
            (key, str(group_id)),
        )
        has_pending = await pending_cursor.fetchone() is not None
        if has_pending or observed_count < min_count:
            return await self._upsert_pending_candidate(
                term=term,
                meaning=meaning,
                aliases=aliases,
                group_id=str(group_id),
                user_id=user_id,
                confidence=confidence,
                evidence=raw_text,
                reason=reason,
                repeat_policy=repeat_policy,
                observed_count=observed_count,
                min_count=min_count,
                meta=candidate_meta,
            )

        now = _now_iso()
        term_id = _new_id("slang")
        candidate_meta = {
            **candidate_meta,
            **await self._attach_normalizer_candidate(
                domain="slang",
                scope="group",
                group_id=str(group_id),
                raw_text=term,
                source_table="slang_terms",
                source_id=term_id,
                message_id=message_id,
                user_id=user_id,
                meta={"path": "new_term_candidate"},
            ),
        }
        unique_users = [user_id] if user_id else []
        await db.execute(
            """INSERT INTO slang_terms
               (term_id, term_key, term, meaning, aliases_json, scope, group_id, confidence,
                status, usage_count, unique_users_json, first_seen_at, last_seen_at,
                last_inferred_at, source, repeat_policy, notes, meta_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'group', ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)""",
            (
                term_id,
                key,
                term.strip(),
                meaning.strip(),
                json.dumps(aliases, ensure_ascii=False),
                str(group_id),
                confidence,
                observed_count,
                json.dumps(unique_users, ensure_ascii=False),
                now,
                now,
                now,
                source,
                repeat_policy,
                json.dumps(candidate_meta, ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.commit()
        await self._invalidate_term_snapshot_cache()
        await self.record_observation(
            term_id,
            group_id=group_id,
            user_id=user_id,
            message_id=message_id,
            raw_text=raw_text,
            context=context,
            reason=reason,
        )
        return term_id

    async def create_term(
        self,
        *,
        term: str,
        meaning: str,
        aliases: list[str] | None = None,
        scope: SlangScope = "group",
        group_id: str = "",
        confidence: float = 0.8,
        status: SlangStatus = "approved",
        repeat_policy: RepeatPolicy = "understand_only",
        notes: str = "",
        evidence: str = "",
        meta: dict[str, Any] | None = None,
        source: str = "manual",
    ) -> SlangTerm:
        """Create a manually curated slang term from the admin console."""
        term_value = str(term or "").strip()
        key = normalize_term(term_value)
        if not key:
            raise ValueError("term cannot be empty")
        if scope not in VALID_SCOPES:
            raise ValueError("invalid scope")
        if status not in VALID_STATUSES:
            raise ValueError("invalid status")
        if repeat_policy not in VALID_REPEAT_POLICIES:
            raise ValueError("invalid repeat_policy")
        normalized_group_id = "" if scope == "global" else str(group_id or "").strip()
        if scope == "group" and not normalized_group_id:
            raise ValueError("group_id is required for group scope")
        aliases = _dedupe(aliases or [])
        if await self.is_stoplisted(term_value, aliases=aliases):
            raise ValueError("term is stoplisted")

        existing = await self.find_existing(
            term=term_value,
            aliases=aliases,
            group_id=normalized_group_id,
            scope=scope,
        )
        if existing is not None:
            raise ValueError("term already exists in this scope")

        now = _now_iso()
        confidence_value = max(0.0, min(1.0, float(confidence)))
        if status == "approved":
            confidence_value = max(confidence_value, 0.8)
        term_id = _new_id("slang")
        term_meta = {
            "manual": source == "manual",
            **(meta or {}),
            **await self._attach_normalizer_candidate(
                domain="slang",
                scope=scope,
                group_id=normalized_group_id or "global",
                raw_text=term_value,
                source_table="slang_terms",
                source_id=term_id,
                meta={"path": "manual_create"},
            ),
        }
        db = self._require_db()
        await db.execute(
            """INSERT INTO slang_terms
               (term_id, term_key, term, meaning, aliases_json, scope, group_id,
                confidence, status, usage_count, unique_users_json, first_seen_at,
                last_seen_at, last_inferred_at, source, repeat_policy, notes, meta_json,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '[]', ?, ?, ?, ?,
                       ?, ?, ?, ?, ?)""",
            (
                term_id,
                key,
                term_value,
                str(meaning or "").strip(),
                json.dumps(_dedupe(aliases or []), ensure_ascii=False),
                scope,
                normalized_group_id,
                confidence_value,
                status,
                now,
                now,
                now,
                str(source or "manual"),
                repeat_policy,
                str(notes or ""),
                json.dumps(term_meta, ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.commit()
        await self._invalidate_term_snapshot_cache()
        if evidence.strip():
            await self.record_observation(
                term_id,
                group_id=normalized_group_id,
                raw_text=evidence.strip(),
                context=evidence.strip(),
                reason="manual_evidence",
            )
        created = await self.get_term(term_id)
        if created is None:
            raise RuntimeError("created slang term cannot be loaded")
        await self.record_revision(
            term_id,
            action="create_term",
            actor="admin" if source == "manual" else str(source or "system"),
            before={},
            after=_term_revision_snapshot(created),
            reason="manual term created" if source == "manual" else "term created",
            meta={"source": source},
        )
        return created

    async def upsert_ai_approved_term(
        self,
        *,
        term: str,
        meaning: str,
        aliases: list[str] | None = None,
        group_id: str,
        user_id: str = "",
        message_id: int | None = None,
        raw_text: str = "",
        context: str = "",
        confidence: float = 0.82,
        reason: str = "",
        repeat_policy: RepeatPolicy = "understand_only",
        meta: dict[str, Any] | None = None,
        observed_count: int = 1,
        settings: SlangSettings | None = None,
    ) -> str | None:
        """Create or promote an AI-reviewed term while keeping approved semantics."""
        key = normalize_term(term)
        if not key or not group_id:
            return None
        settings = settings or await self.load_settings()
        now = _now_iso()
        aliases = _dedupe(aliases or [])
        if await self.is_stoplisted(term, settings, aliases):
            return None
        if await self.is_muted_term(term=term, group_id=str(group_id)):
            return None
        confidence_value = max(0.8, min(1.0, float(confidence)))
        if repeat_policy not in VALID_REPEAT_POLICIES:
            repeat_policy = "understand_only"

        ai_meta = {
            **(meta or {}),
            "ai_approved": True,
            "human_reviewed": False,
            "ai_reviewed_at": now,
            "ai_reason": reason,
            "llm_confidence": confidence_value,
        }
        existing = await self.find_existing(term=term, aliases=aliases, group_id=str(group_id), scope="group")
        if existing and existing.status in {"muted", "expired"}:
            return existing.term_id

        if existing:
            drift_outcome = await self._maybe_create_drift_review(
                existing,
                new_meaning=meaning,
                aliases=aliases,
                evidence=raw_text or context,
                confidence=confidence_value,
                reason=reason or "daily_ai_review",
                meta=ai_meta,
                settings=settings,
            )
            if drift_outcome.drift_id:
                await self.record_hit(
                    existing.term_id,
                    group_id=str(group_id),
                    user_id=user_id,
                    message_id=message_id,
                    raw_text=raw_text,
                    context=context,
                    reason=reason or "daily_ai_review_drift",
                )
                await self._clear_pending_for_key(key, str(group_id), existing.term_id)
                return existing.term_id
            if not drift_outcome.allows_alias_merge and not drift_outcome.allows_meaning_update:
                await self.record_hit(
                    existing.term_id,
                    group_id=str(group_id),
                    user_id=user_id,
                    message_id=message_id,
                    raw_text=raw_text,
                    context=context,
                    reason=reason or "daily_ai_review_drift_gate_suppressed",
                )
                await self._clear_pending_for_key(key, str(group_id), existing.term_id)
                return existing.term_id
            merged_meta = {**existing.meta, **ai_meta}
            if existing.meta.get("human_reviewed") is True:
                merged_meta["human_reviewed"] = True
            updates: dict[str, Any] = {
                "confidence": max(existing.confidence, confidence_value),
                "status": "approved",
                "source": existing.source if existing.meta.get("human_reviewed") else "ai_auto_review",
                "repeat_policy": (
                    repeat_policy if existing.repeat_policy == "understand_only" else existing.repeat_policy
                ),
                "last_inferred_at": now,
                "meta": merged_meta,
                "revision_action": "ai_auto_review",
                "revision_reason": reason,
            }
            if drift_outcome.allows_alias_merge:
                updates["aliases"] = _merge_aliases_for_existing(existing, term, aliases)
            if (
                drift_outcome.allows_meaning_update
                and meaning
                and (not existing.meaning or confidence_value >= existing.confidence)
            ):
                updates["meaning"] = meaning.strip()
            await self.update_term(existing.term_id, **updates)
            await self._merge_pending_candidates_into_existing(
                await self.get_term(existing.term_id) or existing,
                term=term,
                aliases=aliases,
                settings=settings,
            )
            await self.record_hit(
                existing.term_id,
                group_id=str(group_id),
                user_id=user_id,
                message_id=message_id,
                raw_text=raw_text,
                context=context,
                reason=reason or "daily_ai_review",
            )
            await self._clear_pending_for_key(key, str(group_id), existing.term_id)
            return existing.term_id

        term_id = _new_id("slang")
        unique_users = [user_id] if user_id else []
        observed_count = max(1, int(observed_count or 1))
        db = self._require_db()
        await db.execute(
            """INSERT INTO slang_terms
               (term_id, term_key, term, meaning, aliases_json, scope, group_id, confidence,
                status, usage_count, unique_users_json, first_seen_at, last_seen_at,
                last_inferred_at, source, repeat_policy, notes, meta_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'group', ?, ?, 'approved', ?, ?, ?, ?, ?,
                       'ai_auto_review', ?, '', ?, ?, ?)""",
            (
                term_id,
                key,
                str(term or "").strip(),
                str(meaning or "").strip(),
                json.dumps(aliases, ensure_ascii=False),
                str(group_id),
                confidence_value,
                observed_count,
                json.dumps(unique_users, ensure_ascii=False),
                now,
                now,
                now,
                repeat_policy,
                json.dumps(ai_meta, ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.commit()
        await self._invalidate_term_snapshot_cache()
        await self._clear_pending_for_key(key, str(group_id), term_id)
        await self.record_observation(
            term_id,
            group_id=str(group_id),
            user_id=user_id,
            message_id=message_id,
            raw_text=raw_text,
            context=context,
            reason=reason or "daily_ai_review",
        )
        created = await self.get_term(term_id)
        await self.record_revision(
            term_id,
            action="ai_auto_review",
            actor="ai",
            before={},
            after=_term_revision_snapshot(created),
            reason=reason or "daily_ai_review",
            meta=ai_meta,
        )
        return term_id

    async def _clear_pending_for_key(self, term_key: str, group_id: str, target_term_id: str) -> None:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT pending_id FROM slang_pending_candidates WHERE term_key = ? AND group_id = ?",
            (term_key, group_id),
        )
        rows = await cursor.fetchall()
        for row in rows:
            pending_id = row["pending_id"]
            await db.execute(
                "UPDATE slang_observations SET term_id = ? WHERE term_id = ?",
                (target_term_id, pending_id),
            )
            await self._delete_pending_candidate(pending_id)
        await db.commit()

    async def resolve_pending_candidate(self, pending_id: str, target_term_id: str) -> bool:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT 1 FROM slang_pending_candidates WHERE pending_id = ?",
            (pending_id,),
        )
        if await cursor.fetchone() is None:
            return False
        await db.execute(
            "UPDATE slang_observations SET term_id = ? WHERE term_id = ?",
            (target_term_id, pending_id),
        )
        await self._delete_pending_candidate(pending_id)
        await db.commit()
        await self._invalidate_term_snapshot_cache()
        return True

    async def reject_pending_candidate(
        self,
        pending_id: str,
        *,
        group_id: str = "",
        reason: str = "",
        meta: dict[str, Any] | None = None,
    ) -> str | None:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT * FROM slang_pending_candidates WHERE pending_id = ?",
            (pending_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        pending = _row_to_pending(row)
        if group_id and str(pending.group_id) != str(group_id):
            return None

        now = _now_iso()
        reject_meta = {
            **pending.meta,
            **(meta or {}),
            "pending_id": pending.pending_id,
            "ai_rejected": True,
            "review_decision": "denied",
            "reviewed_at": now,
            "ai_reviewed_at": now,
            "ai_reason": reason or pending.reason,
        }
        existing = await self.find_existing(
            term=pending.term,
            aliases=pending.aliases,
            group_id=pending.group_id,
            scope="group",
        )
        if existing is not None:
            await self.resolve_pending_candidate(pending.pending_id, existing.term_id)
            if existing.status == "candidate":
                await self.update_term(
                    existing.term_id,
                    status="muted",
                    meta={**existing.meta, **reject_meta},
                    revision_action="ai_reject_pending",
                    revision_actor="ai",
                    revision_reason=reason or pending.reason or "daily_ai_review_reject",
                    revision_meta=reject_meta,
                )
            return existing.term_id

        term_id = _new_id("slang")
        await db.execute(
            """INSERT INTO slang_terms
               (term_id, term_key, term, meaning, aliases_json, scope, group_id, confidence,
                status, usage_count, unique_users_json, first_seen_at, last_seen_at,
                last_inferred_at, source, repeat_policy, notes, meta_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'group', ?, ?, 'muted', ?, ?, ?, ?, ?,
                       'daily_ai_review', ?, '', ?, ?, ?)""",
            (
                term_id,
                normalize_term(pending.term),
                pending.term,
                pending.meaning,
                json.dumps(pending.aliases, ensure_ascii=False),
                pending.group_id,
                max(0.0, min(1.0, float(pending.confidence or 0.0))),
                max(1, int(pending.count or 1)),
                json.dumps(pending.unique_users, ensure_ascii=False),
                pending.first_seen_at,
                pending.last_seen_at,
                now,
                pending.repeat_policy,
                json.dumps(reject_meta, ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.execute(
            "UPDATE slang_observations SET term_id = ? WHERE term_id = ?",
            (term_id, pending.pending_id),
        )
        await self._delete_pending_candidate(pending.pending_id)
        await db.commit()
        await self._invalidate_term_snapshot_cache()
        created = await self.get_term(term_id)
        await self.record_revision(
            term_id,
            action="ai_reject_pending",
            actor="ai",
            before={},
            after=_term_revision_snapshot(created),
            reason=reason or pending.reason or "daily_ai_review_reject",
            meta=reject_meta,
        )
        return term_id

    async def update_pending_candidate_meta(
        self,
        pending_id: str,
        *,
        meta: dict[str, Any] | None = None,
    ) -> bool:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT meta_json FROM slang_pending_candidates WHERE pending_id = ?",
            (pending_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        current_meta = _json_loads(row["meta_json"], {})
        merged_meta = {**current_meta, **(meta or {})}
        await db.execute(
            """UPDATE slang_pending_candidates
               SET meta_json = ?
               WHERE pending_id = ?""",
            (json.dumps(merged_meta, ensure_ascii=False), pending_id),
        )
        await db.commit()
        return True

    async def promote_pending_candidate(
        self,
        pending_id: str,
        *,
        meta: dict[str, Any] | None = None,
        meaning: str | None = None,
        aliases: list[str] | None = None,
        confidence: float | None = None,
        revision_action: str = "semantic_review_candidate",
        revision_actor: str = "ai",
        revision_reason: str = "",
    ) -> str | None:
        if meta:
            await self.update_pending_candidate_meta(pending_id, meta=meta)
        term_id = await self._promote_pending_candidate(pending_id)
        if term_id is None:
            return None
        if not meta and meaning is None and aliases is None and confidence is None:
            return term_id
        term = await self.get_term(term_id)
        if term is None:
            return term_id
        meta = meta or {}
        updates: dict[str, Any] = {
            "meta": {**term.meta, **meta},
            "last_inferred_at": _now_iso(),
            "revision_action": revision_action,
            "revision_actor": revision_actor,
            "revision_reason": revision_reason or "semantic review promoted pending candidate",
            "revision_meta": meta,
        }
        if meaning is not None:
            updates["meaning"] = meaning
        if aliases is not None:
            updates["aliases"] = aliases
        if confidence is not None:
            updates["confidence"] = confidence
        await self.update_term(
            term_id,
            **updates,
        )
        return term_id

    async def mark_human_reviewed(self, term_id: str, *, reviewer: str = "admin") -> SlangTerm | None:
        term = await self.get_term(term_id)
        if term is None:
            return None
        now = _now_iso()
        meta = {
            **term.meta,
            "human_reviewed": True,
            "review_decision": "approved",
            "reviewed_at": now,
            "human_reviewed_at": now,
            "reviewed_by": str(reviewer or "admin"),
        }
        await self.update_term(
            term_id,
            status="approved",
            confidence=max(term.confidence, 0.8),
            meta=meta,
            revision_action="human_approve",
            revision_actor=str(reviewer or "admin"),
            revision_reason="AI-approved term confirmed by human reviewer",
        )
        return await self.get_term(term_id)

    async def deny_ai_reviewed_term(self, term_id: str, *, reviewer: str = "admin") -> SlangTerm | None:
        term = await self.get_term(term_id)
        if term is None:
            return None
        now = _now_iso()
        meta = {
            **term.meta,
            "human_reviewed": True,
            "review_decision": "denied",
            "denied_at": now,
            "reviewed_at": now,
            "reviewed_by": str(reviewer or "admin"),
        }
        await self.update_term(
            term_id,
            status="muted",
            meta=meta,
            revision_action="human_deny",
            revision_actor=str(reviewer or "admin"),
            revision_reason="AI-approved term denied and muted",
        )
        return await self.get_term(term_id)

    async def return_ai_reviewed_term_to_candidate(
        self,
        term_id: str,
        *,
        reviewer: str = "admin",
    ) -> SlangTerm | None:
        term = await self.get_term(term_id)
        if term is None:
            return None
        now = _now_iso()
        meta = dict(term.meta)
        meta.update(
            {
                "human_reviewed": False,
                "review_decision": "returned_to_candidate",
                "returned_to_candidate_at": now,
                "returned_from_status": term.status,
                "returned_from_source": term.source,
            }
        )
        for key in (
            "ai_approved",
            "ai_rejected",
            "candidate_reviewed",
            "candidate_reviewed_at",
            "candidate_review_complete",
            "candidate_review_decision",
            "candidate_review_decision_confidence",
            "candidate_review_confidence",
            "candidate_review_approved",
            "candidate_review_is_public_meme",
            "candidate_review_reason",
            "candidate_review_state",
            "human_reviewed",
            "human_reviewed_at",
            "ai_reviewed_at",
            "ai_reason",
            "denied_at",
            "reviewed_at",
            "reviewed_by",
        ):
            meta.pop(key, None)
        await self.update_term(
            term_id,
            status="candidate",
            source="admin_returned",
            meta=meta,
            revision_action="return_to_candidate",
            revision_actor=str(reviewer or "admin"),
            revision_reason="AI-approved term returned to candidate queue",
        )
        return await self.get_term(term_id)

    async def record_hit(
        self,
        term_id: str,
        *,
        group_id: str,
        user_id: str = "",
        message_id: int | None = None,
        raw_text: str = "",
        context: str = "",
        reason: str = "message_match",
    ) -> bool:
        return bool(
            await self.record_hits(
                [term_id],
                group_id=group_id,
                user_id=user_id,
                message_id=message_id,
                raw_text=raw_text,
                context=context,
                reason=reason,
            )
        )

    async def record_hits(
        self,
        term_ids: list[str],
        *,
        group_id: str,
        user_id: str = "",
        message_id: int | None = None,
        raw_text: str = "",
        context: str = "",
        reason: str = "message_match",
    ) -> int:
        unique_term_ids: list[str] = []
        seen_ids: set[str] = set()
        for term_id in term_ids:
            key = str(term_id or "").strip()
            if not key or key in seen_ids:
                continue
            seen_ids.add(key)
            unique_term_ids.append(key)
        if not unique_term_ids:
            return 0
        db = self._require_db()
        placeholders = ",".join("?" for _ in unique_term_ids)
        cursor = await db.execute(f"SELECT * FROM slang_terms WHERE term_id IN ({placeholders})", unique_term_ids)
        terms = {row["term_id"]: _row_to_term(row) for row in await cursor.fetchall()}
        observed_term_ids: set[str] = set()
        if message_id is not None:
            cursor = await db.execute(
                f"SELECT term_id FROM slang_observations WHERE message_id = ? AND term_id IN ({placeholders})",
                [message_id, *unique_term_ids],
            )
            observed_term_ids = {str(row["term_id"]) for row in await cursor.fetchall()}
        now = _now_iso()
        changed = 0
        try:
            await db.execute("BEGIN")
            for term_id in unique_term_ids:
                term = terms.get(term_id)
                if term is None or term.status in {"muted", "expired"} or term_id in observed_term_ids:
                    continue
                users = set(term.unique_users)
                if user_id:
                    users.add(str(user_id))
                await db.execute(
                    """UPDATE slang_terms
                       SET usage_count = usage_count + 1,
                           unique_users_json = ?,
                           last_seen_at = ?,
                           updated_at = ?
                       WHERE term_id = ?""",
                    (json.dumps(sorted(users), ensure_ascii=False), now, now, term_id),
                )
                await db.execute(
                    """INSERT INTO slang_observations
                       (observation_id, term_id, group_id, user_id, message_id, raw_text, context, observed_at, reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _new_id("obs"),
                        term_id,
                        str(group_id),
                        str(user_id or ""),
                        message_id,
                        raw_text[:2000],
                        context[:4000],
                        now,
                        reason[:500],
                    ),
                )
                changed += 1
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        if changed:
            await self._invalidate_term_snapshot_cache()
        return changed

    async def _observation_exists(self, term_id: str, message_id: int) -> bool:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT 1 FROM slang_observations WHERE term_id = ? AND message_id = ? LIMIT 1",
            (term_id, message_id),
        )
        return await cursor.fetchone() is not None

    async def record_observation(
        self,
        term_id: str,
        *,
        group_id: str,
        user_id: str = "",
        message_id: int | None = None,
        raw_text: str = "",
        context: str = "",
        reason: str = "",
    ) -> str:
        db = self._require_db()
        observation_id = _new_id("obs")
        await db.execute(
            """INSERT INTO slang_observations
               (observation_id, term_id, group_id, user_id, message_id, raw_text, context, observed_at, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation_id,
                term_id,
                str(group_id),
                str(user_id or ""),
                message_id,
                raw_text[:2000],
                context[:4000],
                _now_iso(),
                reason[:500],
            ),
        )
        await db.commit()
        return observation_id

    async def get_term(self, term_id: str) -> SlangTerm | None:
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM slang_terms WHERE term_id = ?", (term_id,))
        row = await cursor.fetchone()
        return _row_to_term(row) if row else None

    async def list_terms(
        self,
        *,
        group_id: str = "",
        scope: str = "",
        status: str = "",
        search: str = "",
        min_confidence: float | None = None,
        review_filter: str = "",
        limit: int = 50,
        offset: int = 0,
        sort: str = "default",
    ) -> tuple[list[SlangTerm], int]:
        db = self._require_db()
        where: list[str] = []
        values: list[Any] = []
        ai_reviewed = _ai_review_sql_condition()
        human_reviewed = _human_reviewed_sql_condition()
        candidate_reviewed = _candidate_reviewed_sql_condition()
        candidate_review_approved = _candidate_review_approved_sql_condition()
        if group_id:
            where.append("group_id = ?")
            values.append(group_id)
        if scope:
            where.append("scope = ?")
            values.append(scope)
        if status:
            where.append("status = ?")
            values.append(status)
        if search:
            where.append("(term LIKE ? OR meaning LIKE ? OR aliases_json LIKE ?)")
            pattern = f"%{search}%"
            values.extend([pattern, pattern, pattern])
        if min_confidence is not None:
            where.append("confidence >= ?")
            values.append(float(min_confidence))
        if review_filter == "ai_approved":
            where.append(ai_reviewed)
        elif review_filter == "ai_rejected":
            where.append(f"status IN ('candidate', 'muted') AND {_ai_rejected_sql_condition()}")
        elif review_filter == "observe_more":
            where.append(f"status = 'candidate' AND {_candidate_observe_sql_condition()}")
        elif review_filter == "review_failed":
            where.append(f"status = 'candidate' AND {_candidate_review_failed_sql_condition()}")
        elif review_filter == "needs_human_review":
            where.append(f"status = 'approved' AND {ai_reviewed} AND NOT {human_reviewed}")
        elif review_filter == "human_reviewed":
            where.append(human_reviewed)
        elif review_filter == "candidate_ai_unreviewed":
            where.append(f"status = 'candidate' AND NOT {candidate_reviewed}")
        elif review_filter == "candidate_ai_approved":
            where.append(f"status = 'candidate' AND {candidate_review_approved}")
        elif review_filter == "candidate_ai_rejected":
            where.append(f"status IN ('candidate', 'muted') AND {_ai_rejected_sql_condition()}")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        count_cursor = await db.execute(f"SELECT COUNT(*) AS cnt FROM slang_terms {where_sql}", values)
        total = int((await count_cursor.fetchone())["cnt"])
        if sort == "time":
            order_sql = """ORDER BY
                  updated_at DESC,
                  last_seen_at DESC,
                  created_at DESC,
                  confidence DESC,
                  usage_count DESC"""
        else:
            candidate_reviewed = _candidate_reviewed_sql_condition()
            candidate_review_approved = _candidate_review_approved_sql_condition()
            candidate_observe = _candidate_observe_sql_condition()
            candidate_review_failed = _candidate_review_failed_sql_condition()
            ai_rejected = _ai_rejected_sql_condition()
            order_sql = """ORDER BY
                  CASE status WHEN 'candidate' THEN 0 WHEN 'approved' THEN 1 WHEN 'muted' THEN 2 ELSE 3 END,
                  CASE
                    WHEN status = 'candidate' AND NOT ({candidate_reviewed}) THEN 0
                    WHEN status = 'candidate' AND ({candidate_review_approved}) THEN 1
                    WHEN status = 'candidate' AND ({candidate_observe}) THEN 2
                    WHEN status = 'candidate' AND ({candidate_review_failed}) THEN 3
                    WHEN status = 'candidate' AND ({ai_rejected}) THEN 4
                    ELSE 5
                  END,
                  confidence DESC,
                  usage_count DESC,
                  last_seen_at DESC,
                  updated_at DESC"""
            order_sql = order_sql.format(
                candidate_reviewed=candidate_reviewed,
                candidate_review_approved=candidate_review_approved,
                candidate_observe=candidate_observe,
                candidate_review_failed=candidate_review_failed,
                ai_rejected=ai_rejected,
            )
        cursor = await db.execute(
            f"""SELECT * FROM slang_terms {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?""",
            [*values, limit, offset],
        )
        return [_row_to_term(row) for row in await cursor.fetchall()], total

    async def list_observations(self, term_id: str, *, limit: int = 30) -> list[SlangObservation]:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM slang_observations
               WHERE term_id = ?
               ORDER BY observed_at DESC
               LIMIT ?""",
            (term_id, limit),
        )
        return [_row_to_observation(row) for row in await cursor.fetchall()]

    async def list_drift_reviews(
        self,
        *,
        status: str = "open",
        group_id: str = "",
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SlangDriftReview], int]:
        db = self._require_db()
        where: list[str] = []
        values: list[Any] = []
        if status:
            where.append("status = ?")
            values.append(status)
        if group_id:
            where.append("group_id = ?")
            values.append(group_id)
        if search:
            where.append("(term LIKE ? OR old_meaning LIKE ? OR new_meaning LIKE ? OR aliases_json LIKE ?)")
            pattern = f"%{search}%"
            values.extend([pattern, pattern, pattern, pattern])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        count_cursor = await db.execute(f"SELECT COUNT(*) AS cnt FROM slang_drift_reviews {where_sql}", values)
        total = int((await count_cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"""SELECT * FROM slang_drift_reviews {where_sql}
                ORDER BY
                  CASE status WHEN 'open' THEN 0 ELSE 1 END,
                  updated_at DESC,
                  confidence DESC
                LIMIT ? OFFSET ?""",
            [*values, max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))],
        )
        return [_row_to_drift(row) for row in await cursor.fetchall()], total

    async def _get_drift_review(self, drift_id: str) -> SlangDriftReview | None:
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM slang_drift_reviews WHERE drift_id = ?", (drift_id,))
        row = await cursor.fetchone()
        return _row_to_drift(row) if row else None

    async def _set_drift_status(self, drift_id: str, status: str, meta: dict[str, Any] | None = None) -> bool:
        db = self._require_db()
        drift = await self._get_drift_review(drift_id)
        if drift is None:
            return False
        merged_meta = {**drift.meta, **(meta or {})}
        cursor = await db.execute(
            """UPDATE slang_drift_reviews
               SET status = ?, updated_at = ?, meta_json = ?
               WHERE drift_id = ?""",
            (status, _now_iso(), json.dumps(merged_meta, ensure_ascii=False), drift_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def replay_open_drift_reviews(self, *, limit: int = 100, apply: bool = False) -> dict[str, Any]:
        """Re-run the semantic drift gate over open reviews and optionally close false positives."""
        reviewer = self._drift_reviewer
        stats: dict[str, Any] = {
            "ok": reviewer is not None and hasattr(reviewer, "review_drift"),
            "apply": bool(apply),
            "reviewed": 0,
            "closed_same_meaning": 0,
            "aliased": 0,
            "kept_real_drift": 0,
            "kept_unclear": 0,
            "failed": 0,
            "error": "" if reviewer is not None and hasattr(reviewer, "review_drift") else "drift_reviewer_unavailable",
        }
        if not stats["ok"]:
            return stats
        reviews, _total = await self.list_drift_reviews(status="open", limit=max(1, min(int(limit or 100), 200)))
        for drift in reviews:
            term = await self.get_term(drift.term_id)
            if term is None:
                stats["failed"] += 1
                if apply:
                    await self._set_drift_status(
                        drift.drift_id,
                        "rejected",
                        {"semantic_replay": True, "semantic_replay_error": "missing_term"},
                    )
                continue
            try:
                assessment = await reviewer.review_drift(
                    existing=term,
                    new_meaning=drift.new_meaning,
                    aliases=drift.aliases,
                    evidence=drift.evidence,
                    confidence=drift.confidence,
                    reason=drift.reason,
                )
            except Exception as exc:
                assessment = SlangDriftAssessment(
                    verdict="unclear",
                    reviewed=True,
                    error=f"drift_replay_failed:{exc}",
                )
            stats["reviewed"] += 1
            meta = {**assessment.to_meta(), "semantic_replay": True}
            if assessment.verdict == "same_meaning":
                stats["closed_same_meaning"] += 1
                if apply:
                    await self.reject_drift_review(
                        drift.drift_id,
                        reviewer="semantic_drift_replay",
                        meta=meta,
                    )
            elif assessment.verdict == "alias_candidate":
                stats["aliased"] += 1
                if apply:
                    await self.alias_drift_review(drift.drift_id, reviewer="semantic_drift_replay")
                    await self._set_drift_status(drift.drift_id, "aliased", meta)
            elif assessment.verdict == "real_drift":
                stats["kept_real_drift"] += 1
                if apply:
                    await self._set_drift_status(drift.drift_id, "open", meta)
            else:
                stats["kept_unclear"] += 1
                if apply:
                    await self._set_drift_status(drift.drift_id, "open", meta)
        return stats

    async def accept_drift_review(self, drift_id: str, *, reviewer: str = "admin") -> SlangTerm | None:
        drift = await self._get_drift_review(drift_id)
        if drift is None:
            return None
        term = await self.get_term(drift.term_id)
        if term is None:
            await self._set_drift_status(drift_id, "accepted", {"missing_term": True})
            return None
        meta = {
            **term.meta,
            "last_drift_review": {
                "drift_id": drift.drift_id,
                "decision": "accepted",
                "reviewed_at": _now_iso(),
                "reviewed_by": reviewer,
            },
        }
        await self.update_term(
            term.term_id,
            meaning=drift.new_meaning,
            aliases=_dedupe([*term.aliases, *drift.aliases]),
            confidence=max(term.confidence, drift.confidence),
            last_inferred_at=_now_iso(),
            meta=meta,
            revision_action="drift_accept",
            revision_actor=reviewer,
            revision_reason=drift.reason,
            revision_meta={"drift_id": drift.drift_id, "old_meaning": drift.old_meaning},
        )
        await self._set_drift_status(drift_id, "accepted", {"reviewed_by": reviewer})
        return await self.get_term(term.term_id)

    async def reject_drift_review(
        self,
        drift_id: str,
        *,
        reviewer: str = "admin",
        meta: dict[str, Any] | None = None,
    ) -> bool:
        drift = await self._get_drift_review(drift_id)
        if drift is None:
            return False
        term = await self.get_term(drift.term_id)
        if term is not None:
            await self.record_revision(
                term.term_id,
                action="drift_reject",
                actor=reviewer,
                before=_term_revision_snapshot(term),
                after=_term_revision_snapshot(term),
                reason=drift.reason or "drift rejected",
                meta={"drift_id": drift.drift_id, "new_meaning": drift.new_meaning, **(meta or {})},
            )
        return await self._set_drift_status(drift_id, "rejected", {"reviewed_by": reviewer, **(meta or {})})

    async def alias_drift_review(self, drift_id: str, *, reviewer: str = "admin") -> SlangTerm | None:
        drift = await self._get_drift_review(drift_id)
        if drift is None:
            return None
        term = await self.get_term(drift.term_id)
        if term is None:
            await self._set_drift_status(drift_id, "aliased", {"missing_term": True})
            return None
        alias_values = _dedupe([*term.aliases, drift.term, *drift.aliases])
        meta = {
            **term.meta,
            "last_drift_review": {
                "drift_id": drift.drift_id,
                "decision": "alias",
                "reviewed_at": _now_iso(),
                "reviewed_by": reviewer,
            },
        }
        await self.update_term(
            term.term_id,
            aliases=alias_values,
            meta=meta,
            revision_action="drift_alias",
            revision_actor=reviewer,
            revision_reason=drift.reason,
            revision_meta={"drift_id": drift.drift_id, "new_meaning": drift.new_meaning},
        )
        await self._set_drift_status(drift_id, "aliased", {"reviewed_by": reviewer})
        return await self.get_term(term.term_id)

    async def mute_drift_review(self, drift_id: str, *, reviewer: str = "admin") -> SlangTerm | None:
        drift = await self._get_drift_review(drift_id)
        if drift is None:
            return None
        term = await self.get_term(drift.term_id)
        if term is None:
            await self._set_drift_status(drift_id, "muted", {"missing_term": True})
            return None
        await self.update_term(
            term.term_id,
            status="muted",
            revision_action="drift_mute",
            revision_actor=reviewer,
            revision_reason=drift.reason or "drift muted",
            revision_meta={"drift_id": drift.drift_id, "new_meaning": drift.new_meaning},
        )
        await self._set_drift_status(drift_id, "muted", {"reviewed_by": reviewer})
        return await self.get_term(term.term_id)

    async def update_term(self, term_id: str, **fields: Any) -> bool:
        revision_action = str(fields.pop("revision_action", "update_term") or "update_term")
        revision_actor = str(fields.pop("revision_actor", "system") or "system")
        revision_reason = str(fields.pop("revision_reason", "") or "")
        revision_meta = fields.pop("revision_meta", None)
        record_revision = bool(fields.pop("record_revision", True))
        allowed = {
            "term", "meaning", "aliases", "scope", "group_id", "confidence", "status",
            "source", "repeat_policy", "notes", "meta", "last_inferred_at",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return False
        term = await self.get_term(term_id)
        if term is None:
            return False
        before_snapshot = _term_revision_snapshot(term)

        normalized: dict[str, Any] = {}
        if "term" in updates:
            term_value = str(updates["term"]).strip()
            key = normalize_term(term_value)
            if not key:
                raise ValueError("term cannot be empty")
            normalized["term"] = term_value
            normalized["term_key"] = key
        if "meaning" in updates:
            normalized["meaning"] = str(updates["meaning"]).strip()
        if "aliases" in updates:
            aliases = updates["aliases"]
            if isinstance(aliases, str):
                aliases = [part.strip() for part in re.split(r"[,，\n]", aliases) if part.strip()]
            normalized["aliases_json"] = json.dumps(_dedupe(list(aliases or [])), ensure_ascii=False)
        if "scope" in updates:
            scope = str(updates["scope"])
            if scope not in VALID_SCOPES:
                raise ValueError("invalid scope")
            normalized["scope"] = scope
            if scope == "global":
                normalized["group_id"] = ""
        if "group_id" in updates and updates.get("scope", term.scope) != "global":
            normalized["group_id"] = str(updates["group_id"] or "").strip()
        if "confidence" in updates:
            normalized["confidence"] = max(0.0, min(1.0, float(updates["confidence"])))
        if "status" in updates:
            status = str(updates["status"])
            if status not in VALID_STATUSES:
                raise ValueError("invalid status")
            normalized["status"] = status
        if "source" in updates:
            normalized["source"] = str(updates["source"] or "extractor").strip() or "extractor"
        if "repeat_policy" in updates:
            policy = str(updates["repeat_policy"])
            if policy not in VALID_REPEAT_POLICIES:
                raise ValueError("invalid repeat_policy")
            normalized["repeat_policy"] = policy
        if "notes" in updates:
            normalized["notes"] = str(updates["notes"] or "")
        if "meta" in updates:
            normalized["meta_json"] = json.dumps(updates["meta"] or {}, ensure_ascii=False)
        if "last_inferred_at" in updates:
            normalized["last_inferred_at"] = updates["last_inferred_at"]

        normalized["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{key} = ?" for key in normalized)
        db = self._require_db()
        cursor = await db.execute(
            f"UPDATE slang_terms SET {set_clause} WHERE term_id = ?",
            [*normalized.values(), term_id],
        )
        await db.commit()
        changed = cursor.rowcount > 0
        if changed and record_revision:
            after = await self.get_term(term_id)
            after_snapshot = _term_revision_snapshot(after)
            if before_snapshot != after_snapshot:
                await self.record_revision(
                    term_id,
                    action=revision_action,
                    actor=revision_actor,
                    before=before_snapshot,
                    after=after_snapshot,
                    reason=revision_reason,
                    meta=revision_meta if isinstance(revision_meta, dict) else {},
                )
        if changed:
            await self._invalidate_term_snapshot_cache()
        return changed

    async def set_status(
        self,
        term_id: str,
        status: SlangStatus,
        *,
        actor: str = "admin",
        reason: str = "",
    ) -> bool:
        if status not in VALID_STATUSES:
            raise ValueError("invalid status")
        updates: dict[str, Any] = {
            "status": status,
            "revision_action": f"set_status:{status}",
            "revision_actor": actor,
            "revision_reason": reason,
        }
        if status == "approved":
            term = await self.get_term(term_id)
            updates["confidence"] = max(term.confidence if term else 0.0, 0.8)
        return await self.update_term(term_id, **updates)

    async def bulk_set_status(self, term_ids: list[str], status: SlangStatus) -> dict[str, int]:
        changed = 0
        for term_id in term_ids:
            if await self.set_status(term_id, status, actor="admin", reason="bulk_action"):
                changed += 1
        return {"requested": len(term_ids), "changed": changed}

    async def delete_observations_for_terms(self, term_ids: list[str]) -> int:
        if not term_ids:
            return 0
        placeholders = ",".join("?" for _ in term_ids)
        db = self._require_db()
        cursor = await db.execute(
            f"DELETE FROM slang_observations WHERE term_id IN ({placeholders})",
            term_ids,
        )
        await db.commit()
        return cursor.rowcount

    async def recompute_confidence(self, term_id: str) -> SlangTerm | None:
        term = await self.get_term(term_id)
        if term is None:
            return None
        count_score = min(term.usage_count / 20, 1.0) * 0.3
        user_score = min(term.unique_user_count / 6, 1.0) * 0.25
        llm_score = min(float(term.meta.get("llm_confidence", term.confidence) or 0.0), 1.0) * 0.2
        status_score = 0.2 if term.status == "approved" else 0.0
        recency_score = 0.05 if term.last_seen_at else 0.0
        score = round(min(1.0, count_score + user_score + llm_score + status_score + recency_score), 3)
        meta = {
            **term.meta,
            "confidence_signals": {
                "usage_count": round(count_score, 3),
                "unique_users": round(user_score, 3),
                "llm": round(llm_score, 3),
                "status": round(status_score, 3),
                "recency": round(recency_score, 3),
            },
        }
        await self.update_term(term_id, confidence=score, meta=meta)
        return await self.get_term(term_id)

    async def merge_terms(self, *, target_id: str, source_ids: list[str]) -> SlangTerm | None:
        target = await self.get_term(target_id)
        if target is None:
            return None
        target_before = _term_revision_snapshot(target)
        sources: list[SlangTerm] = []
        for term_id in source_ids:
            if term_id == target_id:
                continue
            source = await self.get_term(term_id)
            if source is not None:
                sources.append(source)
        if not sources:
            return target
        aliases = _dedupe([
            *target.aliases,
            *(source.term for source in sources),
            *(alias for source in sources for alias in source.aliases),
        ])
        users = set(target.unique_users)
        usage_count = target.usage_count
        confidence = target.confidence
        merged_from = list(target.meta.get("merged_from", []))
        db = self._require_db()
        for source in sources:
            users.update(source.unique_users)
            usage_count += source.usage_count
            confidence = max(confidence, source.confidence)
            merged_from.append(source.term_id)
            await db.execute(
                "UPDATE slang_observations SET term_id = ? WHERE term_id = ?",
                (target_id, source.term_id),
            )
            source_meta = {**source.meta, "merged_into": target_id}
            await self.update_term(
                source.term_id,
                status="expired",
                meta=source_meta,
                revision_action="merge_source_expired",
                revision_reason=f"merged into {target_id}",
                revision_meta={"merged_into": target_id},
            )
        meta = {**target.meta, "merged_from": _dedupe(merged_from)}
        now = _now_iso()
        await db.execute(
            """UPDATE slang_terms
               SET aliases_json = ?, usage_count = ?, unique_users_json = ?,
                   confidence = ?, meta_json = ?, updated_at = ?, last_seen_at = ?
               WHERE term_id = ?""",
            (
                json.dumps(aliases, ensure_ascii=False),
                usage_count,
                json.dumps(sorted(users), ensure_ascii=False),
                confidence,
                json.dumps(meta, ensure_ascii=False),
                now,
                max([target.last_seen_at, *(source.last_seen_at for source in sources)]),
                target_id,
            ),
        )
        await db.commit()
        await self._invalidate_term_snapshot_cache()
        merged = await self.get_term(target_id)
        await self.record_revision(
            target_id,
            action="merge_terms",
            actor="admin",
            before=target_before,
            after=_term_revision_snapshot(merged),
            reason="merged duplicate slang terms",
            meta={"source_ids": [source.term_id for source in sources]},
        )
        return merged

    async def list_pending(
        self,
        *,
        group_id: str = "",
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SlangPendingCandidate], int]:
        db = self._require_db()
        where: list[str] = []
        values: list[Any] = []
        if group_id:
            where.append("group_id = ?")
            values.append(group_id)
        if search:
            where.append("(term LIKE ? OR meaning LIKE ? OR aliases_json LIKE ?)")
            pattern = f"%{search}%"
            values.extend([pattern, pattern, pattern])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        cursor = await db.execute(f"SELECT COUNT(*) AS cnt FROM slang_pending_candidates {where_sql}", values)
        total = int((await cursor.fetchone())["cnt"])
        cursor = await db.execute(
            f"""SELECT * FROM slang_pending_candidates {where_sql}
                ORDER BY last_seen_at DESC, count DESC, confidence DESC
                LIMIT ? OFFSET ?""",
            [*values, limit, offset],
        )
        return [_row_to_pending(row) for row in await cursor.fetchall()], total

    async def start_extraction_run(self, *, group_count: int = 0, meta: dict[str, Any] | None = None) -> str:
        run_id = _new_id("run")
        db = self._require_db()
        await db.execute(
            """INSERT INTO slang_extraction_runs
               (run_id, started_at, status, group_count, meta_json)
               VALUES (?, ?, 'running', ?, ?)""",
            (run_id, _now_iso(), group_count, json.dumps(meta or {}, ensure_ascii=False)),
        )
        await db.commit()
        return run_id

    async def finish_extraction_run(
        self,
        run_id: str,
        *,
        status: str = "success",
        group_count: int = 0,
        scanned_messages: int = 0,
        extracted_terms: int = 0,
        promoted_candidates: int = 0,
        error: str = "",
        meta: dict[str, Any] | None = None,
    ) -> None:
        db = self._require_db()
        cursor = await db.execute("SELECT started_at, meta_json FROM slang_extraction_runs WHERE run_id = ?", (run_id,))
        row = await cursor.fetchone()
        started = datetime.now(TZ_SHANGHAI)
        existing_meta: dict[str, Any] = {}
        if row is not None:
            with contextlib.suppress(Exception):
                started = datetime.fromisoformat(row["started_at"])
            existing_meta = _json_loads(row["meta_json"], {})
        finished = datetime.now(TZ_SHANGHAI)
        duration_ms = max(0, int((finished - started).total_seconds() * 1000))
        await db.execute(
            """UPDATE slang_extraction_runs
               SET finished_at = ?, status = ?, group_count = ?, scanned_messages = ?,
                   extracted_terms = ?, promoted_candidates = ?, error = ?,
                   duration_ms = ?, meta_json = ?
               WHERE run_id = ?""",
            (
                finished.isoformat(timespec="seconds"),
                status,
                group_count,
                scanned_messages,
                extracted_terms,
                promoted_candidates,
                error[:1000],
                duration_ms,
                json.dumps({**existing_meta, **(meta or {})}, ensure_ascii=False),
                run_id,
            ),
        )
        await db.commit()

    async def list_extraction_runs(self, *, limit: int = 10) -> list[SlangExtractionRun]:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM slang_extraction_runs
               ORDER BY started_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [_row_to_run(row) for row in await cursor.fetchall()]

    async def list_running_extraction_runs(
        self,
        *,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[SlangExtractionRun]:
        db = self._require_db()
        if kind:
            cursor = await db.execute(
                """SELECT * FROM slang_extraction_runs
                   WHERE status = 'running'
                     AND json_extract(meta_json, '$.kind') = ?
                   ORDER BY started_at ASC
                   LIMIT ?""",
                (kind, limit),
            )
        else:
            cursor = await db.execute(
                """SELECT * FROM slang_extraction_runs
                   WHERE status = 'running'
                   ORDER BY started_at ASC
                   LIMIT ?""",
                (limit,),
            )
        return [_row_to_run(row) for row in await cursor.fetchall()]

    async def list_stale_extraction_runs(
        self,
        *,
        kind: str,
        stale_before_iso: str,
        limit: int = 20,
    ) -> list[SlangExtractionRun]:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM slang_extraction_runs
               WHERE status = 'running'
                 AND started_at < ?
                 AND json_extract(meta_json, '$.kind') = ?
               ORDER BY started_at ASC
               LIMIT ?""",
            (stale_before_iso, kind, limit),
        )
        return [_row_to_run(row) for row in await cursor.fetchall()]

    async def has_successful_extraction_run(
        self,
        *,
        kind: str,
        started_date: str,
    ) -> bool:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT 1 FROM slang_extraction_runs
               WHERE status = 'success'
                 AND substr(started_at, 1, 10) = ?
                 AND json_extract(meta_json, '$.kind') = ?
               LIMIT 1""",
            (started_date, kind),
        )
        return await cursor.fetchone() is not None

    async def abandon_extraction_run(
        self,
        run_id: str,
        *,
        reason: str = "stale_run_recovered",
        meta: dict[str, Any] | None = None,
    ) -> None:
        await self.finish_extraction_run(
            run_id,
            status="abandoned",
            error=reason,
            meta={"abandoned": True, "abandon_reason": reason, **(meta or {})},
        )

    async def scan_global_candidates(self, *, min_groups: int = 3) -> dict[str, Any]:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM slang_terms
               WHERE scope = 'group' AND status IN ('candidate', 'approved')"""
        )
        groups: dict[str, list[SlangTerm]] = {}
        alias_sources: dict[str, list[SlangTerm]] = {}
        for row in await cursor.fetchall():
            term = _row_to_term(row)
            term_key = normalize_term(term.term)
            if term_key:
                groups.setdefault(term_key, []).append(term)
            for alias in term.aliases:
                alias_key = normalize_term(alias)
                if alias_key:
                    alias_sources.setdefault(alias_key, []).append(term)
        created = 0
        skipped = 0
        seen_source_sets: set[tuple[str, ...]] = set()
        for key, terms in groups.items():
            terms = list({term.term_id: term for term in terms}.values())
            unique_groups = {term.group_id for term in terms if term.group_id}
            if not key or len(unique_groups) < min_groups:
                continue
            source_set = tuple(sorted(term.term_id for term in terms))
            if source_set in seen_source_sets:
                skipped += 1
                continue
            seen_source_sets.add(source_set)
            related_alias_terms = alias_sources.get(key, [])
            aliases = _dedupe(
                [term.term for term in terms]
                + [alias for term in terms for alias in term.aliases]
                + [term.term for term in related_alias_terms]
            )
            existing_global = await self.find_existing(
                term=terms[0].term,
                aliases=aliases,
                group_id="",
                scope="global",
            )
            if existing_global:
                skipped += 1
                continue
            representative = sorted(terms, key=lambda item: (item.confidence, item.usage_count), reverse=True)[0]
            similar_terms = [
                term for term in terms
                if not representative.meaning
                or not term.meaning
                or _ngram_similarity(representative.meaning, term.meaning) >= 0.16
            ]
            similar_groups = {term.group_id for term in similar_terms if term.group_id}
            if len(similar_groups) < min_groups:
                skipped += 1
                continue
            terms = similar_terms
            aliases = _dedupe([term.term for term in terms] + [alias for term in terms for alias in term.aliases])
            meanings = [term.meaning for term in terms if term.meaning]
            meaning = representative.meaning or (meanings[0] if meanings else "")
            now = _now_iso()
            term_id = _new_id("slang")
            await db.execute(
                """INSERT INTO slang_terms
                   (term_id, term_key, term, meaning, aliases_json, scope, group_id,
                    confidence, status, usage_count, unique_users_json, first_seen_at,
                    last_seen_at, last_inferred_at, source, repeat_policy, notes, meta_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'global', '', ?, 'candidate', ?, ?, ?, ?, ?,
                           'global_scan', ?, '', ?, ?, ?)""",
                (
                    term_id,
                    key,
                    representative.term,
                    meaning,
                    json.dumps(aliases, ensure_ascii=False),
                    max(term.confidence for term in terms),
                    sum(term.usage_count for term in terms),
                    json.dumps(sorted({user for term in terms for user in term.unique_users}), ensure_ascii=False),
                    min(term.first_seen_at for term in terms),
                    max(term.last_seen_at for term in terms),
                    now,
                    representative.repeat_policy,
                    json.dumps({
                        "global_candidate": True,
                        "source_term_ids": [term.term_id for term in terms],
                        "source_groups": sorted(unique_groups),
                        "alias_source_term_ids": [term.term_id for term in related_alias_terms],
                    }, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            created += 1
        await db.commit()
        if created:
            await self._invalidate_term_snapshot_cache()
        return {"ok": True, "created": created, "skipped": skipped}

    async def _load_group_term_snapshot(self, group_id: str, *, include_global: bool = True) -> list[SlangTerm]:
        cached = self._get_cached_term_snapshot(group_id)
        if include_global and cached is not None:
            return cached
        db = self._require_db()
        if include_global:
            cursor = await db.execute(
                """SELECT * FROM slang_terms
                   WHERE scope = 'global' OR (scope = 'group' AND group_id = ?)
                   ORDER BY
                     CASE status WHEN 'approved' THEN 0 WHEN 'candidate' THEN 1 WHEN 'muted' THEN 2 ELSE 3 END,
                     confidence DESC,
                     usage_count DESC,
                     updated_at DESC""",
                (group_id,),
            )
        else:
            cursor = await db.execute(
                """SELECT * FROM slang_terms
                   WHERE scope = 'group' AND group_id = ?
                   ORDER BY
                     CASE status WHEN 'approved' THEN 0 WHEN 'candidate' THEN 1 WHEN 'muted' THEN 2 ELSE 3 END,
                     confidence DESC,
                     usage_count DESC,
                     updated_at DESC""",
                (group_id,),
            )
        terms = [_row_to_term(row) for row in await cursor.fetchall()]
        if include_global:
            self._set_cached_term_snapshot(group_id, terms)
        return terms

    async def find_matching_terms(
        self,
        *,
        group_id: str,
        text: str,
        include_candidates: bool = True,
    ) -> list[SlangTerm]:
        if not group_id or not text:
            return []
        statuses = {"approved", "candidate"} if include_candidates else {"approved"}
        settings = await self.load_settings()
        stop_keys = {normalize_term(item) for item in settings.stoplist}
        stop_keys = {key for key in stop_keys if key}
        result: list[SlangTerm] = []
        for term in await self._load_group_term_snapshot(
            str(group_id),
            include_global=settings.allows_global_terms(group_id),
        ):
            if term.status not in statuses:
                continue
            if _matches_any_key(term.term, term.aliases, stop_keys):
                continue
            candidates = [term.term, *term.aliases]
            for candidate in candidates:
                if matches_slang_candidate(candidate, text):
                    result.append(term)
                    break
        return result

    async def get_injectable_terms(
        self,
        *,
        group_id: str,
        conversation_text: str = "",
        max_terms: int = 8,
        min_confidence: float = 0.0,
    ) -> list[SlangTerm]:
        if not group_id:
            return []
        settings = await self.load_settings()
        stop_keys = {normalize_term(item) for item in settings.stoplist}
        stop_keys = {key for key in stop_keys if key}
        terms = [
            term
            for term in await self._load_group_term_snapshot(
                str(group_id),
                include_global=settings.allows_global_terms(group_id),
            )
            if term.status == "approved"
            and term.confidence >= max(0.0, min(1.0, float(min_confidence or 0.0)))
            and not _matches_any_key(term.term, term.aliases, stop_keys)
        ]

        def score(term: SlangTerm) -> tuple[int, int, float, int, str]:
            names = [term.term, *term.aliases]
            direct = any(matches_slang_candidate(name, conversation_text) for name in names)
            scope_priority = 1 if term.scope == "group" else 0
            return (1 if direct else 0, scope_priority, term.confidence, term.usage_count, term.last_seen_at)

        terms.sort(key=score, reverse=True)
        return terms[:max_terms]

    async def lookup_terms(
        self,
        *,
        group_id: str | None,
        query: str,
        limit: int = 6,
        min_confidence: float = 0.0,
    ) -> list[SlangTerm]:
        db = self._require_db()
        query_value = str(query or "").strip()
        key = normalize_term(query_value)
        min_conf = max(0.0, min(1.0, float(min_confidence or 0.0)))
        settings = await self.load_settings()
        stop_keys = {normalize_term(item) for item in settings.stoplist}
        stop_keys = {item for item in stop_keys if item}
        where = ["status = 'approved'", "confidence >= ?"]
        values: list[Any] = [min_conf]
        if group_id:
            if settings.allows_global_terms(group_id):
                where.append("(scope = 'global' OR (scope = 'group' AND group_id = ?))")
            else:
                where.append("(scope = 'group' AND group_id = ?)")
            values.append(str(group_id))
        else:
            where.append("scope = 'global'")
        if query_value:
            where.append("(term LIKE ? OR meaning LIKE ? OR aliases_json LIKE ?)")
            pattern = f"%{query_value}%"
            values.extend([pattern, pattern, pattern])
        cursor = await db.execute(
            f"""SELECT * FROM slang_terms
                WHERE {' AND '.join(where)}
                ORDER BY confidence DESC, usage_count DESC, updated_at DESC
                LIMIT ?""",
            [*values, max(1, min(int(limit or 6), 20))],
        )
        terms = [
            term
            for term in (_row_to_term(row) for row in await cursor.fetchall())
            if not _matches_any_key(term.term, term.aliases, stop_keys)
        ]
        if terms or not key:
            return terms
        if not group_id:
            scope_sql = "scope = 'global'"
            scope_values: list[Any] = []
        else:
            scope_sql = (
                "(scope = 'global' OR (scope = 'group' AND group_id = ?))"
                if settings.allows_global_terms(group_id)
                else "(scope = 'group' AND group_id = ?)"
            )
            scope_values = [str(group_id)]
        cursor = await db.execute(
            f"""SELECT * FROM slang_terms
                WHERE status = 'approved'
                  AND confidence >= ?
                  AND {scope_sql}
                ORDER BY confidence DESC, usage_count DESC, updated_at DESC
                LIMIT 100""",
            [min_conf, *scope_values],
        )
        matches: list[SlangTerm] = []
        for row in await cursor.fetchall():
            term = _row_to_term(row)
            if _matches_any_key(term.term, term.aliases, stop_keys):
                continue
            names = [term.term, *term.aliases]
            meaning_similarity = _ngram_similarity(query_value, term.meaning)
            if (
                any(matches_slang_candidate(name, query_value) for name in names)
                or meaning_similarity >= 0.2
            ):
                matches.append(term)
        matches.sort(key=lambda item: (item.confidence, item.usage_count), reverse=True)
        return matches[:max(1, min(int(limit or 6), 20))]

    async def build_prompt_block(
        self,
        *,
        group_id: str,
        conversation_text: str = "",
        max_terms: int = 8,
        max_chars: int = 1200,
    ) -> str:
        conversation_text = str(conversation_text or "").strip()
        if not conversation_text:
            return ""
        terms = await self.get_injectable_terms(
            group_id=group_id,
            conversation_text=conversation_text,
            max_terms=max_terms,
            min_confidence=(await self.load_settings()).min_inject_confidence,
        )
        direct_terms = [
            term
            for term in terms
            if any(matches_slang_candidate(name, conversation_text) for name in [term.term, *term.aliases])
        ]
        if not direct_terms:
            return ""
        lines = [
            "以下是当前群本轮上下文命中的已批准黑话。优先用于理解群聊上下文，不要为了显得懂梗而强行复述。",
        ]
        for term in direct_terms:
            aliases = f"；别名：{'、'.join(term.aliases[:5])}" if term.aliases else ""
            policy = {
                "understand_only": "仅理解，不主动复述",
                "allow_rephrase": "可换一种自然说法解释",
                "allow_use": "可在合适语境自然使用",
            }.get(term.repeat_policy, "仅理解，不主动复述")
            next_line = f"- {term.term}{aliases}：{term.meaning or '含义待补充'}（{policy}）"
            if len("\n".join([*lines, next_line])) > max_chars:
                break
            lines.append(next_line)
        return "\n".join(lines)

    async def stats(self, *, days: int = 14) -> dict[str, Any]:
        """Return lightweight review and activity stats for the admin console."""
        db = self._require_db()
        days = max(1, min(int(days or 14), 120))
        since_date = (datetime.now(TZ_SHANGHAI) - timedelta(days=days - 1)).date().isoformat()

        cursor = await db.execute(
            """SELECT * FROM slang_terms
               ORDER BY usage_count DESC, confidence DESC, updated_at DESC
               LIMIT 8"""
        )
        popular_terms = [_row_to_term(row) for row in await cursor.fetchall()]

        cursor = await db.execute(
            """SELECT
                 group_id,
                 COUNT(*) AS term_count,
                 SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
                 SUM(usage_count) AS usage_count
               FROM slang_terms
               WHERE group_id != ''
               GROUP BY group_id
               ORDER BY usage_count DESC, approved_count DESC, term_count DESC
               LIMIT 8"""
        )
        group_activity = [
            {
                "group_id": str(row["group_id"]),
                "term_count": int(row["term_count"] or 0),
                "approved_count": int(row["approved_count"] or 0),
                "usage_count": int(row["usage_count"] or 0),
            }
            for row in await cursor.fetchall()
        ]

        cursor = await db.execute(
            """SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS cnt
               FROM slang_terms
               WHERE created_at >= ?
               GROUP BY day""",
            (since_date,),
        )
        created_by_day = {str(row["day"]): int(row["cnt"] or 0) for row in await cursor.fetchall()}
        cursor = await db.execute(
            """SELECT substr(observed_at, 1, 10) AS day, COUNT(*) AS cnt
               FROM slang_observations
               WHERE observed_at >= ?
               GROUP BY day""",
            (since_date,),
        )
        observed_by_day = {str(row["day"]): int(row["cnt"] or 0) for row in await cursor.fetchall()}
        trend: list[dict[str, Any]] = []
        start_day = datetime.fromisoformat(since_date).date()
        for index in range(days):
            day = (start_day + timedelta(days=index)).isoformat()
            trend.append({
                "date": day,
                "created": created_by_day.get(day, 0),
                "observations": observed_by_day.get(day, 0),
            })

        cursor = await db.execute("SELECT status, COUNT(*) AS cnt FROM slang_terms GROUP BY status")
        status_counts = {str(row["status"]): int(row["cnt"] or 0) for row in await cursor.fetchall()}
        reviewed = status_counts.get("approved", 0) + status_counts.get("muted", 0) + status_counts.get("expired", 0)
        total_terms = sum(status_counts.values())
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM slang_pending_candidates")
        pending_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM slang_drift_reviews WHERE status = 'open'")
        drift_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute("SELECT AVG(confidence) AS avg_conf FROM slang_terms WHERE status = 'approved'")
        avg_confidence = float((await cursor.fetchone())["avg_conf"] or 0.0)
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM slang_terms WHERE scope = 'global' AND status = 'candidate'"
        )
        global_candidates = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM slang_terms WHERE scope = 'global' AND status = 'approved'"
        )
        global_approved = int((await cursor.fetchone())["cnt"] or 0)

        return {
            "popular_terms": [_term_stats_dict(term) for term in popular_terms],
            "group_activity": group_activity,
            "recent_trend": trend,
            "review": {
                "total_terms": total_terms,
                "candidate_count": status_counts.get("candidate", 0),
                "drift_count": drift_count,
                "reviewed_count": reviewed,
                "approval_rate": round(status_counts.get("approved", 0) / reviewed, 3) if reviewed else 0.0,
            },
            "injection": {
                "approved_terms": status_counts.get("approved", 0),
                "avg_confidence": round(avg_confidence, 3),
                "global_candidates": global_candidates,
                "global_approved": global_approved,
                "observing_count": pending_count,
            },
        }

    async def summary(self) -> dict[str, Any]:
        db = self._require_db()
        counts: dict[str, int] = {status: 0 for status in VALID_STATUSES}
        cursor = await db.execute("SELECT status, COUNT(*) AS cnt FROM slang_terms GROUP BY status")
        for row in await cursor.fetchall():
            counts[str(row["status"])] = int(row["cnt"])

        today_prefix = datetime.now(TZ_SHANGHAI).date().isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM slang_observations WHERE observed_at LIKE ?",
            (f"{today_prefix}%",),
        )
        today_hits = int((await cursor.fetchone())["cnt"])
        cursor = await db.execute("SELECT COUNT(DISTINCT group_id) AS cnt FROM slang_terms WHERE group_id != ''")
        group_count = int((await cursor.fetchone())["cnt"])
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM slang_pending_candidates")
        pending_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM slang_drift_reviews WHERE status = 'open'")
        drift_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            """SELECT * FROM slang_extraction_runs
               ORDER BY started_at DESC
               LIMIT 1"""
        )
        run_row = await cursor.fetchone()
        latest_run = _row_to_run(run_row) if run_row else None
        ai_reviewed = _ai_review_sql_condition()
        human_reviewed = _human_reviewed_sql_condition()
        ai_rejected = _ai_rejected_sql_condition()
        cursor = await db.execute(
            f"SELECT COUNT(*) AS cnt FROM slang_terms WHERE status = 'approved' AND {ai_reviewed}"
        )
        ai_review_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS cnt FROM slang_terms
                WHERE status = 'approved' AND {ai_reviewed} AND NOT {human_reviewed}"""
        )
        ai_pending_review_count = int((await cursor.fetchone())["cnt"] or 0)
        candidate_reviewed = _candidate_reviewed_sql_condition()
        candidate_review_approved = _candidate_review_approved_sql_condition()
        candidate_review_observe = _candidate_observe_sql_condition()
        candidate_review_failed = _candidate_review_failed_sql_condition()
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS cnt FROM slang_terms
                WHERE status = 'candidate' AND {candidate_reviewed}"""
        )
        candidate_reviewed_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS cnt FROM slang_terms
                WHERE status = 'candidate' AND NOT {candidate_reviewed}"""
        )
        candidate_unreviewed_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS cnt FROM slang_terms
                WHERE status = 'candidate' AND {candidate_review_approved}"""
        )
        candidate_review_approved_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS cnt FROM slang_terms
                WHERE status IN ('candidate', 'muted') AND {ai_rejected}"""
        )
        candidate_review_rejected_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS cnt FROM slang_terms
                WHERE status = 'candidate' AND {candidate_review_observe}"""
        )
        candidate_review_kept_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS cnt FROM slang_terms
                WHERE status = 'candidate' AND {candidate_review_failed}"""
        )
        candidate_review_failed_count = int((await cursor.fetchone())["cnt"] or 0)
        candidate_total_count = counts.get("candidate", 0)
        return {
            # Backward compatibility: old admin bundles used candidate_count for
            # the "待审核" card. Keep that legacy field fail-closed as the
            # actually unreviewed count so cached JS cannot display all
            # candidates as unreviewed again.
            "candidate_count": candidate_unreviewed_count,
            "candidate_total_count": candidate_total_count,
            "candidate_reviewed_count": candidate_reviewed_count,
            "candidate_unreviewed_count": candidate_unreviewed_count,
            "candidate_review_approved_count": candidate_review_approved_count,
            "candidate_review_rejected_count": candidate_review_rejected_count,
            "candidate_review_kept_count": candidate_review_kept_count,
            "candidate_review_failed_count": candidate_review_failed_count,
            "approved_count": counts.get("approved", 0),
            "muted_count": counts.get("muted", 0),
            "expired_count": counts.get("expired", 0),
            "pending_count": pending_count,
            "drift_count": drift_count,
            "ai_review_count": ai_review_count,
            "ai_pending_review_count": ai_pending_review_count,
            "today_hits": today_hits,
            "group_count": group_count,
            "last_extracted_at": await self.get_meta("last_extracted_at", ""),
            "last_daily_ai_review_at": await self.get_meta("last_daily_ai_review_at", ""),
            "latest_run_status": latest_run.status if latest_run else "",
        }

    async def list_groups(self) -> list[str]:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT DISTINCT group_id FROM slang_terms WHERE group_id != '' ORDER BY group_id"
        )
        return [str(row["group_id"]) for row in await cursor.fetchall()]
