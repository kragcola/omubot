"""SQLite store for Omubot group slang terms."""

from __future__ import annotations

import contextlib
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite
from loguru import logger

from services.cross_group import cross_group_where
from services.similarity import NgramSimilarityProvider, normalize_text_key
from services.slang.errors import (
    SlangCollisionError,
    SlangCrossScopeMergeError,
    SlangDatabaseCorruptError,
)
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
from services.storage import close_with_checkpoint, connect_sqlite

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

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
    "CREATE INDEX IF NOT EXISTS idx_slang_runs_started ON slang_extraction_runs(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_slang_revisions_term ON slang_term_revisions(term_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_slang_drift_status ON slang_drift_reviews(status, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_slang_drift_term ON slang_drift_reviews(term_id, status)",
]


async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    names = {str(row["name"]) for row in await cursor.fetchall()}
    if column not in names:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def normalize_term(value: str) -> str:
    """Normalize a slang term for lookup and merging."""
    return normalize_text_key(value)


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
        cross_group_visible=bool(row["cross_group_visible"]),
        cross_group_enabled_by=row["cross_group_enabled_by"] or "",
        cross_group_enabled_at=row["cross_group_enabled_at"] or "",
        cross_group_enabled_for_groups=[
            str(item)
            for item in (_json_loads(row["cross_group_enabled_for_groups"], []) or [])
            if str(item).strip()
        ],
        cross_group_enabled_reason=row["cross_group_enabled_reason"] or "",
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


def _ai_review_sql_condition() -> str:
    """Legacy name — detects AI-approved terms. Use _ai_approved_sql_condition() for new code."""
    return _ai_approved_sql_condition()


def _ai_approved_sql_condition() -> str:
    """AI conclusion is 'approved'. Covers daily (ai_approved=true) and new contract (ai_review_decision='approved')."""
    return (
        "(source = 'ai_auto_review' "
        "OR meta_json LIKE '%\"ai_approved\": true%' "
        "OR meta_json LIKE '%\"ai_approved\":true%' "
        "OR meta_json LIKE '%\"ai_review_decision\": \"approved\"%' "
        "OR meta_json LIKE '%\"ai_review_decision\":\"approved\"%')"
    )


def _ai_reviewed_sql_condition() -> str:
    """AI has reviewed this term (regardless of conclusion). Detects ai_reviewed_at timestamp."""
    return (
        "(source = 'ai_auto_review' "
        "OR meta_json LIKE '%\"ai_reviewed_at\": \"%' "
        "OR meta_json LIKE '%\"ai_reviewed_at\":\"%')"
    )


# Frequency-stair gating: a term re-enters the backlog reviewer only when its
# usage_count crosses one of these thresholds since its last AI review. Reviewed
# terms that haven't re-crossed a threshold are skipped to save tokens.
_BACKLOG_THRESHOLD_GATE_SQL = (
    " AND ("
    "  (meta_json NOT LIKE '%\"last_inference_count\":%')"
    "  OR ("
    "    CAST(json_extract(meta_json, '$.last_inference_count') AS INTEGER) < 3 AND usage_count >= 3"
    "  ) OR ("
    "    CAST(json_extract(meta_json, '$.last_inference_count') AS INTEGER) < 8 AND usage_count >= 8"
    "  ) OR ("
    "    CAST(json_extract(meta_json, '$.last_inference_count') AS INTEGER) < 30 AND usage_count >= 30"
    "  ) OR ("
    "    CAST(json_extract(meta_json, '$.last_inference_count') AS INTEGER) < 100 AND usage_count >= 100"
    "  )"
    ")"
)


def _ai_rejected_sql_condition() -> str:
    """AI conclusion is 'rejected' (explicit deny, not kept/ambiguous)."""
    return (
        "(meta_json LIKE '%\"ai_review_decision\": \"rejected\"%' "
        "OR meta_json LIKE '%\"ai_review_decision\":\"rejected\"%')"
    )


def _ai_kept_sql_condition() -> str:
    """AI conclusion is 'kept' (ambiguous, under observation)."""
    return (
        "(meta_json LIKE '%\"ai_review_decision\": \"kept\"%' "
        "OR meta_json LIKE '%\"ai_review_decision\":\"kept\"%')"
    )


def _human_reviewed_sql_condition() -> str:
    return (
        "(meta_json LIKE '%\"human_reviewed\": true%' "
        "OR meta_json LIKE '%\"human_reviewed\":true%')"
    )


class SlangStore:
    """Manage slang terms, observations, and runtime settings."""

    def __init__(
        self,
        db_path: str | Path = "storage/slang.db",
        *,
        drift_reviewer: Any = None,
    ) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._drift_reviewer: Any = drift_reviewer
        self._hit_listeners: list[
            Callable[[str, str, str, int], Awaitable[None]]
        ] = []

    def add_hit_listener(
        self,
        listener: Callable[[str, str, str, int], Awaitable[None]],
    ) -> None:
        """Register a coroutine called after each successful ``record_hit``.

        Listener signature: ``async (term_id, group_id, user_id, usage_count) -> None``.
        Failures inside listeners are caught by ``_fire_hit_listeners`` and
        logged as warnings — they MUST never break the source-of-truth path.
        Used by the Phase E.1 graph bridge to mirror term-group hits as
        ``term_used_in_group`` edges.
        """
        self._hit_listeners.append(listener)

    async def _fire_hit_listeners(
        self,
        term_id: str,
        group_id: str,
        user_id: str,
        usage_count: int,
    ) -> None:
        for listener in self._hit_listeners:
            try:
                await listener(term_id, group_id, user_id, usage_count)
            except Exception as exc:
                logger.warning(
                    "slang hit listener failed | term={} group={} listener={} err={}",
                    term_id,
                    group_id,
                    getattr(listener, "__qualname__", repr(listener)),
                    exc,
                )

    def set_drift_reviewer(self, drift_reviewer: Any) -> None:
        """Allow late binding once the LLM client is wired into the plugin."""
        self._drift_reviewer = drift_reviewer

    async def init(self) -> None:
        db: aiosqlite.Connection | None = None
        try:
            db = await connect_sqlite(self._db_path)
            # slang.db is write-light, read-heavy. macOS Docker bind-mount + WAL has
            # a known fsync ordering hazard that has caused recurring corruption
            # every 5–10 days. Switching to DELETE journal + synchronous=FULL drops
            # the WAL surface entirely; cost is acceptable at slang's write volume.
            await db.execute("PRAGMA journal_mode=DELETE")
            await db.execute("PRAGMA synchronous=FULL")
            await db.commit()
            await db.execute(_CREATE_TERMS)
            await db.execute(_CREATE_OBSERVATIONS)
            await db.execute(_CREATE_SETTINGS)
            await db.execute(_CREATE_PENDING)
            await db.execute(_CREATE_RUNS)
            await db.execute(_CREATE_REVISIONS)
            await db.execute(_CREATE_DRIFT_REVIEWS)
            for statement in _INDEXES:
                await db.execute(statement)
            # Cross-group visibility migration (A2)
            await _ensure_column(db, "slang_terms", "cross_group_visible", "INTEGER NOT NULL DEFAULT 0")
            await _ensure_column(db, "slang_terms", "cross_group_enabled_by", "TEXT NOT NULL DEFAULT ''")
            await _ensure_column(db, "slang_terms", "cross_group_enabled_at", "TEXT NOT NULL DEFAULT ''")
            await _ensure_column(
                db, "slang_terms", "cross_group_enabled_for_groups", "TEXT NOT NULL DEFAULT '[]'"
            )
            await _ensure_column(
                db, "slang_terms", "cross_group_enabled_reason", "TEXT NOT NULL DEFAULT ''"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_slang_cross_group "
                "ON slang_terms(cross_group_visible, status) "
                "WHERE cross_group_visible = 1"
            )
            await db.commit()
        except aiosqlite.DatabaseError as exc:
            if db is not None:
                with contextlib.suppress(Exception):
                    await db.close()
            raise SlangDatabaseCorruptError(self._db_path, original=exc) from exc
        self._db = db

    async def close(self) -> None:
        if self._db is not None:
            await close_with_checkpoint(self._db, name="slang")
            self._db = None

    @property
    def initialized(self) -> bool:
        return self._db is not None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SlangStore is not initialized")
        return self._db

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
    ) -> str | None:
        settings = settings or await self.load_settings()
        if not settings.drift_detection_enabled:
            return None
        if existing.status != "approved" or confidence < settings.drift_min_confidence:
            return None
        new_meaning = str(new_meaning or "").strip()
        if not new_meaning or not existing.meaning.strip():
            return None
        similarity = _ngram_similarity(existing.meaning, new_meaning)
        if similarity >= 0.28:
            return None
        db = self._require_db()
        cursor = await db.execute(
            """SELECT drift_id, meta_json FROM slang_drift_reviews
               WHERE term_id = ? AND status = 'open'
               ORDER BY updated_at DESC
               LIMIT 1""",
            (existing.term_id,),
        )
        row = await cursor.fetchone()
        now = _now_iso()
        drift_meta = {
            **(meta or {}),
            "meaning_similarity": round(similarity, 3),
            "source_status": existing.status,
        }
        if row is not None:
            drift_id = row["drift_id"]
            existing_drift_meta = _json_loads(row["meta_json"], {})
            if not isinstance(existing_drift_meta, dict):
                existing_drift_meta = {}
            already_reviewed = bool(existing_drift_meta.get("drift_semantic_reviewed"))
            # Existing open row: run the LLM gate at most once. Subsequent
            # UPDATEs after the gate has already classified it skip the LLM
            # call but still refresh new_meaning / evidence on the row so the
            # admin keeps seeing the latest evidence text.
            gate_decision: str | None = None
            if (
                not already_reviewed
                and settings.drift_semantic_gate_enabled
                and self._drift_reviewer is not None
            ):
                try:
                    assessment = await self._drift_reviewer.review_drift(
                        existing=existing,
                        new_meaning=new_meaning,
                        aliases=aliases,
                        evidence=evidence,
                        confidence=confidence,
                        reason=reason,
                    )
                except Exception:
                    assessment = None
                if assessment is not None and assessment.reviewed and not assessment.error:
                    drift_meta.update(assessment.to_meta())
                    if assessment.verdict == "same_meaning":
                        gate_decision = "auto_dismissed"
                    elif assessment.verdict == "alias_candidate":
                        gate_decision = "auto_aliased"
                    else:
                        gate_decision = "reviewed_real_drift"
            merged_drift_meta = {**existing_drift_meta, **drift_meta}
            if gate_decision == "auto_dismissed":
                merged_drift_meta["drift_semantic_reviewed"] = True
                await db.execute(
                    """UPDATE slang_drift_reviews
                       SET new_meaning = ?, aliases_json = ?, evidence = ?, confidence = ?,
                           reason = ?, updated_at = ?, meta_json = ?, status = 'auto_dismissed'
                       WHERE drift_id = ?""",
                    (
                        new_meaning,
                        json.dumps(_dedupe([*existing.aliases, *aliases]), ensure_ascii=False),
                        str(evidence or "")[:2000],
                        max(0.0, min(1.0, confidence)),
                        str(reason or "")[:800],
                        now,
                        json.dumps(merged_drift_meta, ensure_ascii=False),
                        drift_id,
                    ),
                )
                await db.commit()
                await self.record_revision(
                    existing.term_id,
                    action="drift_dismissed",
                    after={"drift_id": drift_id, "verdict": "same_meaning"},
                    reason=str(merged_drift_meta.get("drift_semantic_reason") or "drift_semantic_same_meaning"),
                    meta=merged_drift_meta,
                )
                return None
            if gate_decision == "auto_aliased":
                merged_drift_meta["drift_semantic_reviewed"] = True
                merged_aliases = _dedupe([*existing.aliases, *aliases])
                if merged_aliases != existing.aliases:
                    await self.update_term(
                        existing.term_id,
                        aliases=merged_aliases,
                        revision_action="drift_aliased_auto",
                        revision_reason=str(merged_drift_meta.get("drift_semantic_reason") or "drift_semantic_alias"),
                        revision_meta=merged_drift_meta,
                    )
                await db.execute(
                    """UPDATE slang_drift_reviews
                       SET new_meaning = ?, aliases_json = ?, evidence = ?, confidence = ?,
                           reason = ?, updated_at = ?, meta_json = ?, status = 'auto_aliased'
                       WHERE drift_id = ?""",
                    (
                        new_meaning,
                        json.dumps(merged_aliases, ensure_ascii=False),
                        str(evidence or "")[:2000],
                        max(0.0, min(1.0, confidence)),
                        str(reason or "")[:800],
                        now,
                        json.dumps(merged_drift_meta, ensure_ascii=False),
                        drift_id,
                    ),
                )
                await db.commit()
                return None
            # real_drift / unclear / no gate run: keep status 'open', just UPDATE.
            # If the gate did run and returned a decisive verdict (real_drift /
            # unclear), stamp the meta so we don't re-pay LLM next pass.
            if gate_decision == "reviewed_real_drift":
                merged_drift_meta["drift_semantic_reviewed"] = True
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
                    json.dumps(merged_drift_meta, ensure_ascii=False),
                    drift_id,
                ),
            )
            await db.commit()
            return drift_id

        # First-time drift detection: run LLM semantic gate before creating a row.
        # Existing open records skip this gate (already reviewed once) so we don't
        # re-pay LLM cost on every update.
        if settings.drift_semantic_gate_enabled and self._drift_reviewer is not None:
            try:
                assessment = await self._drift_reviewer.review_drift(
                    existing=existing,
                    new_meaning=new_meaning,
                    aliases=aliases,
                    evidence=evidence,
                    confidence=confidence,
                    reason=reason,
                )
            except Exception:
                assessment = None
            if assessment is not None and assessment.reviewed and not assessment.error:
                drift_meta.update(assessment.to_meta())
                if assessment.verdict == "same_meaning":
                    await self.record_revision(
                        existing.term_id,
                        action="drift_dismissed",
                        before=_term_revision_snapshot(existing),
                        after={
                            "new_meaning": new_meaning,
                            "verdict": assessment.verdict,
                            "confidence": confidence,
                        },
                        reason=assessment.reason or "drift_semantic_same_meaning",
                        meta=drift_meta,
                    )
                    return None
                if assessment.verdict == "alias_candidate":
                    merged_aliases = _dedupe([*existing.aliases, *aliases])
                    if merged_aliases != existing.aliases:
                        await self.update_term(
                            existing.term_id,
                            aliases=merged_aliases,
                            revision_action="drift_aliased_auto",
                            revision_reason=assessment.reason or "drift_semantic_alias",
                            revision_meta=drift_meta,
                        )
                    else:
                        await self.record_revision(
                            existing.term_id,
                            action="drift_aliased_auto",
                            before=_term_revision_snapshot(existing),
                            after={
                                "new_meaning": new_meaning,
                                "verdict": assessment.verdict,
                                "aliases": merged_aliases,
                            },
                            reason=assessment.reason or "drift_semantic_alias",
                            meta=drift_meta,
                        )
                    return None
                # real_drift / unclear → fall through and create the row.
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
        return drift_id

    async def load_settings(self) -> SlangSettings:
        db = self._require_db()
        cursor = await db.execute("SELECT value_json FROM slang_settings WHERE key = 'settings'")
        row = await cursor.fetchone()
        if row is None:
            settings = SlangSettings()
            await self.save_settings(settings)
            return settings
        data = _json_loads(row["value_json"], {})
        try:
            return SlangSettings.model_validate(data)
        except Exception:
            return SlangSettings()

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
        return model

    async def is_stoplisted(self, term: str, settings: SlangSettings | None = None) -> bool:
        settings = settings or await self.load_settings()
        key = normalize_term(term)
        if not key:
            return True
        stop_keys = {normalize_term(item) for item in settings.stoplist}
        return key in stop_keys

    async def is_muted_term(self, *, term: str, group_id: str) -> bool:
        existing = await self.find_existing(term=term, group_id=group_id, scope="group")
        if existing and existing.status == "muted":
            return True
        global_existing = await self.find_existing(term=term, group_id="", scope="global")
        return bool(global_existing and global_existing.status == "muted")

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
        exclude_term_ids: list[str] | None = None,
    ) -> SlangTerm | None:
        """Locate an existing term whose key OR any alias collides with the
        candidate's key OR any of its aliases.

        Symmetric lookup: the candidate's `term` and every entry in `aliases`
        are folded into a single set of normalized keys, then we match against
        both stored `term_key` and stored `aliases_json`.  This prevents the
        mirror-collision pattern (A:[B] vs B:[A]) where two candidates each
        list the other's primary key as an alias and neither side detects
        duplication.

        When ``exclude_term_ids`` is provided, any match whose ``term_id`` is
        in the exclusion set is skipped — used by ``update_term`` /
        ``merge_terms`` to avoid self-collision.
        """
        key = normalize_term(term)
        candidate_keys: set[str] = set()
        if key:
            candidate_keys.add(key)
        for alias in aliases or []:
            normalized = normalize_term(alias)
            if normalized:
                candidate_keys.add(normalized)
        if not candidate_keys:
            return None
        db = self._require_db()
        scope_group = "" if scope == "global" else group_id
        excluded = set(exclude_term_ids or ())

        # 1. Direct term_key hit, prefer matching the primary key first so the
        #    return order is deterministic when the candidate's own key already
        #    exists (most common case).
        if key:
            cursor = await db.execute(
                "SELECT * FROM slang_terms WHERE term_key = ? AND scope = ? AND group_id = ?",
                (key, scope, scope_group),
            )
            row = await cursor.fetchone()
            if row:
                hit = _row_to_term(row)
                if hit.term_id not in excluded:
                    return hit

        # 2. Any alias key matches an existing term_key.
        alias_keys_only = candidate_keys - ({key} if key else set())
        if alias_keys_only:
            placeholders = ",".join("?" for _ in alias_keys_only)
            cursor = await db.execute(
                f"SELECT * FROM slang_terms "
                f"WHERE scope = ? AND group_id = ? AND term_key IN ({placeholders})",
                (scope, scope_group, *alias_keys_only),
            )
            for row in await cursor.fetchall():
                hit = _row_to_term(row)
                if hit.term_id not in excluded:
                    return hit

        # 3. Any candidate key (term or alias) appears in an existing term's
        #    alias list. We can't index aliases_json, so scope-bound full scan.
        cursor = await db.execute(
            "SELECT * FROM slang_terms WHERE scope = ? AND group_id = ?",
            (scope, scope_group),
        )
        for stored in await cursor.fetchall():
            term_obj = _row_to_term(stored)
            if term_obj.term_id in excluded:
                continue
            stored_alias_keys = {normalize_term(alias) for alias in term_obj.aliases}
            stored_alias_keys.discard("")
            if candidate_keys & stored_alias_keys:
                return term_obj
        return None

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
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
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
        merged_meta = {**pending.meta, **(meta or {})}
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
            group_id=pending.group_id,
            scope="group",
            aliases=pending.aliases,
        )
        if existing:
            await db.execute("DELETE FROM slang_pending_candidates WHERE pending_id = ?", (pending_id,))
            await db.commit()
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
        await db.execute("DELETE FROM slang_pending_candidates WHERE pending_id = ?", (pending_id,))
        await db.commit()
        return term_id

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
        if await self.is_stoplisted(term, settings):
            return None
        if await self.is_muted_term(term=term, group_id=str(group_id)):
            return None
        aliases = _dedupe(aliases or [])
        confidence = max(0.0, min(1.0, float(confidence)))
        candidate_meta = {**(meta or {}), "llm_confidence": confidence}
        if repeat_policy not in VALID_REPEAT_POLICIES:
            repeat_policy = "understand_only"

        existing = await self.find_existing(
            term=term, group_id=group_id, scope="group", aliases=aliases,
        )
        if existing and existing.status in {"muted", "expired"}:
            return existing.term_id
        if existing:
            drift_id = await self._maybe_create_drift_review(
                existing,
                new_meaning=meaning,
                aliases=aliases,
                evidence=raw_text or context,
                confidence=confidence,
                reason=reason,
                meta=candidate_meta,
                settings=settings,
            )
            if drift_id:
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
            merged_aliases = _dedupe([*existing.aliases, *aliases])
            updates: dict[str, Any] = {
                "aliases": merged_aliases,
                "confidence": max(existing.confidence, confidence),
                "last_inferred_at": _now_iso(),
                "revision_action": "candidate_update",
                "revision_reason": reason,
            }
            if meaning and (not existing.meaning or confidence >= existing.confidence):
                updates["meaning"] = meaning.strip()
            if repeat_policy and existing.repeat_policy == "understand_only":
                updates["repeat_policy"] = repeat_policy
            await self.update_term(existing.term_id, **updates)
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

    async def _attach_normalizer(
        self,
        *,
        term_id: str,
        raw_text: str,
        scope: SlangScope,
        group_id: str,
        user_id: str = "",
        message_id: int | None = None,
    ) -> None:
        """Attach a slang term to the learning normalizer for dedup clustering."""
        from services.learning_normalizer import LearningNormalizerStore

        normalizer: LearningNormalizerStore | None = None
        try:
            db_dir = Path(self._db_path).parent
            normalizer_path = str(db_dir / "learning_normalizer.db")
            normalizer = LearningNormalizerStore(normalizer_path)
            await normalizer.init()
            await normalizer.attach_candidate(
                domain="slang",
                scope=scope,
                group_id=group_id,
                raw_text=raw_text,
                source_table="slang_terms",
                source_id=term_id,
                message_id=message_id,
                user_id=user_id,
                profile="slang",
            )
        except Exception:
            pass
        finally:
            if normalizer is not None:
                with contextlib.suppress(Exception):
                    await normalizer.close()

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

        existing = await self.find_existing(
            term=term_value,
            group_id=normalized_group_id,
            scope=scope,
            aliases=aliases or [],
        )
        if existing is not None:
            raise ValueError("term already exists in this scope")

        now = _now_iso()
        confidence_value = max(0.0, min(1.0, float(confidence)))
        if status == "approved":
            confidence_value = max(confidence_value, 0.8)
        term_id = _new_id("slang")
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
                json.dumps(
                    {
                        "manual": source == "manual",
                        **(meta or {}),
                        **({"human_reviewed": True, "reviewed_at": now} if source == "manual" else {}),
                    },
                    ensure_ascii=False,
                ),
                now,
                now,
            ),
        )
        await db.commit()
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
        await self._attach_normalizer(
            term_id=term_id,
            raw_text=f"{term_value} {meaning}",
            scope=scope,
            group_id=normalized_group_id,
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
        if await self.is_stoplisted(term, settings):
            return None
        if await self.is_muted_term(term=term, group_id=str(group_id)):
            return None

        now = _now_iso()
        aliases = _dedupe(aliases or [])
        confidence_value = max(0.8, min(1.0, float(confidence)))
        if repeat_policy not in VALID_REPEAT_POLICIES:
            repeat_policy = "understand_only"

        ai_meta = {
            **(meta or {}),
            "ai_approved": True,
            "human_reviewed": False,
            "ai_reviewed_at": now,
            "ai_review_source": "daily",
            "ai_review_decision": "approved",
            "ai_reason": reason,
            "llm_confidence": confidence_value,
        }
        existing = await self.find_existing(
            term=term, group_id=str(group_id), scope="group", aliases=aliases,
        )
        if existing and existing.status in {"muted", "expired"}:
            return existing.term_id

        if existing:
            drift_id = await self._maybe_create_drift_review(
                existing,
                new_meaning=meaning,
                aliases=aliases,
                evidence=raw_text or context,
                confidence=confidence_value,
                reason=reason or "daily_ai_review",
                meta=ai_meta,
                settings=settings,
            )
            if drift_id:
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
            merged_aliases = _dedupe([*existing.aliases, *aliases])
            merged_meta = {**existing.meta, **ai_meta}
            if existing.meta.get("human_reviewed") is True:
                merged_meta["human_reviewed"] = True
            updates: dict[str, Any] = {
                "aliases": merged_aliases,
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
            if meaning and (not existing.meaning or confidence_value >= existing.confidence):
                updates["meaning"] = meaning.strip()
            await self.update_term(existing.term_id, **updates)
            await self._attach_normalizer(
                term_id=existing.term_id,
                raw_text=f"{existing.term} {updates.get('meaning', existing.meaning)}",
                scope="group",
                group_id=str(group_id),
                user_id=user_id,
                message_id=message_id,
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
        await self._attach_normalizer(
            term_id=term_id,
            raw_text=f"{term} {meaning}",
            scope="group",
            group_id=str(group_id),
            user_id=user_id,
            message_id=message_id,
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
            await db.execute("DELETE FROM slang_pending_candidates WHERE pending_id = ?", (pending_id,))
        await db.commit()

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

    async def return_ai_reviewed_term_to_candidate(self, term_id: str) -> SlangTerm | None:
        term = await self.get_term(term_id)
        if term is None:
            return None
        meta = {
            **term.meta,
            "human_reviewed": False,
            "review_decision": "returned_to_candidate",
            "returned_to_candidate_at": _now_iso(),
        }
        await self.update_term(
            term_id,
            status="candidate",
            meta=meta,
            revision_action="return_to_candidate",
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
        term = await self.get_term(term_id)
        if term is None or term.status in {"muted", "expired"}:
            return False
        if message_id is not None and await self._observation_exists(term_id, message_id):
            return False
        users = set(term.unique_users)
        if user_id:
            users.add(str(user_id))
        now = _now_iso()
        db = self._require_db()
        await db.execute(
            """UPDATE slang_terms
               SET usage_count = usage_count + 1,
                   unique_users_json = ?,
                   last_seen_at = ?,
                   updated_at = ?
               WHERE term_id = ?""",
            (json.dumps(sorted(users), ensure_ascii=False), now, now, term_id),
        )
        await db.commit()
        await self.record_observation(
            term_id,
            group_id=group_id,
            user_id=user_id,
            message_id=message_id,
            raw_text=raw_text,
            context=context,
            reason=reason,
        )
        await self._fire_hit_listeners(
            term_id, group_id, user_id, term.usage_count + 1
        )
        return True

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
        sort_by: str = "updated_desc",
    ) -> tuple[list[SlangTerm], int]:
        db = self._require_db()
        where: list[str] = []
        values: list[Any] = []
        ai_reviewed = _ai_review_sql_condition()
        human_reviewed = _human_reviewed_sql_condition()
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
        ai_reviewed_any = _ai_reviewed_sql_condition()
        ai_rejected = _ai_rejected_sql_condition()
        ai_kept = _ai_kept_sql_condition()
        if review_filter == "ai_approved":
            where.append(ai_reviewed)
        elif review_filter == "unreviewed":
            where.append(f"NOT {ai_reviewed_any}")
        elif review_filter == "pending_all":
            where.append(f"status = 'candidate' AND (NOT {ai_reviewed_any} OR {ai_kept})")
        elif review_filter == "needs_human_review":
            where.append(f"status = 'approved' AND {ai_reviewed} AND NOT {human_reviewed}")
        elif review_filter == "human_reviewed":
            where.append(human_reviewed)
        elif review_filter == "human_reviewed_only":
            where.append(f"status = 'approved' AND {human_reviewed}")
        elif review_filter == "under_observation":
            where.append(f"status = 'candidate' AND {ai_reviewed_any} AND {ai_kept}")
        elif review_filter == "ai_rejected_only":
            where.append(f"status = 'muted' AND {ai_rejected} AND NOT {human_reviewed}")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        count_cursor = await db.execute(f"SELECT COUNT(*) AS cnt FROM slang_terms {where_sql}", values)
        total = int((await count_cursor.fetchone())["cnt"])
        # Sort presets — secondary keys keep ordering stable when the primary key ties.
        sort_clauses: dict[str, str] = {
            "updated_desc": "updated_at DESC, confidence DESC, term_id DESC",
            "updated_asc": "updated_at ASC, confidence DESC, term_id DESC",
            "confidence_desc": "confidence DESC, usage_count DESC, updated_at DESC, term_id DESC",
            "usage_desc": "usage_count DESC, confidence DESC, updated_at DESC, term_id DESC",
        }
        order_sql = sort_clauses.get(sort_by, sort_clauses["updated_desc"])
        cursor = await db.execute(
            f"""SELECT * FROM slang_terms {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?""",
            [*values, limit, offset],
        )
        return [_row_to_term(row) for row in await cursor.fetchall()], total

    async def count_backlog_candidates(
        self,
        *,
        min_confidence: float = 0.0,
        min_usage_count: int = 1,
        gated_by_threshold: bool = False,
    ) -> int:
        """Count slang_terms still in candidate state above a confidence floor.

        When ``gated_by_threshold`` is True, also restrict to terms that have
        either never been reviewed or whose ``usage_count`` has crossed a
        new threshold in [3, 8, 30, 100] since the last review. This
        avoids re-reviewing terms whose evidence base hasn't grown.
        """
        db = self._require_db()
        gate_sql, gate_params = (
            (_BACKLOG_THRESHOLD_GATE_SQL, ())
            if gated_by_threshold
            else ("", ())
        )
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM slang_terms"
            " WHERE status = 'candidate' AND confidence >= ? AND usage_count >= ?"
            + gate_sql,
            (float(min_confidence), int(min_usage_count), *gate_params),
        )
        row = await cursor.fetchone()
        return int(row["cnt"] if row else 0)

    async def list_backlog_candidates(
        self,
        *,
        after_term_id: str = "",
        min_confidence: float = 0.0,
        min_usage_count: int = 1,
        limit: int = 50,
        gated_by_threshold: bool = False,
    ) -> list[SlangTerm]:
        """Cursor-paginated fetch of candidate-state terms for the AI backlog reviewer.

        Sort key: confidence DESC, usage_count DESC, term_id DESC. The cursor is the
        full sort tuple of the last term processed, encoded as ``after_term_id`` —
        callers pass back the previous batch's last ``term_id`` and we resume from
        the next row in the same total order. Avoids OFFSET so concurrent mute /
        approve writes don't shift the window and skip rows.
        """
        db = self._require_db()
        gate_sql = _BACKLOG_THRESHOLD_GATE_SQL if gated_by_threshold else ""
        if after_term_id:
            cursor = await db.execute(
                "SELECT confidence, usage_count, term_id FROM slang_terms WHERE term_id = ?",
                (after_term_id,),
            )
            anchor = await cursor.fetchone()
        else:
            anchor = None
        if anchor is None:
            cursor = await db.execute(
                f"""SELECT * FROM slang_terms
                   WHERE status = 'candidate' AND confidence >= ? AND usage_count >= ?
                   {gate_sql}
                   ORDER BY confidence DESC, usage_count DESC, term_id DESC
                   LIMIT ?""",
                (float(min_confidence), int(min_usage_count), int(limit)),
            )
        else:
            cursor = await db.execute(
                f"""SELECT * FROM slang_terms
                   WHERE status = 'candidate' AND confidence >= ? AND usage_count >= ?
                     AND (
                       confidence < ?
                       OR (confidence = ? AND usage_count < ?)
                       OR (confidence = ? AND usage_count = ? AND term_id < ?)
                     )
                   {gate_sql}
                   ORDER BY confidence DESC, usage_count DESC, term_id DESC
                   LIMIT ?""",
                (
                    float(min_confidence),
                    int(min_usage_count),
                    anchor["confidence"],
                    anchor["confidence"], anchor["usage_count"],
                    anchor["confidence"], anchor["usage_count"], anchor["term_id"],
                    int(limit),
                ),
            )
        return [_row_to_term(row) for row in await cursor.fetchall()]

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
                  confidence DESC,
                  updated_at DESC
                LIMIT ? OFFSET ?""",
            [*values, max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))],
        )
        return [_row_to_drift(row) for row in await cursor.fetchall()], total

    async def process_open_drift_backlog(
        self,
        *,
        limit: int = 100,
        force: bool = False,
    ) -> dict[str, int]:
        """Run the LLM semantic gate over existing ``open`` drifts.

        For each row that hasn't been gated yet (``meta.drift_semantic_reviewed``
        absent), call ``SlangDriftReviewer.review_drift`` once and apply:
        - ``same_meaning`` → status ``auto_dismissed``
        - ``alias_candidate`` → status ``auto_aliased`` + merge aliases
        - ``real_drift`` / ``unclear`` → keep ``open`` but stamp meta so we
          don't re-pay LLM next pass

        ``force=True`` re-gates rows even if previously stamped (for retries
        after a model upgrade). Returns a counter dict the admin UI can show.
        """
        result = {"scanned": 0, "dismissed": 0, "aliased": 0, "kept_real_drift": 0, "skipped": 0, "errors": 0}
        if self._drift_reviewer is None:
            result["errors"] = 1
            return result
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM slang_drift_reviews
               WHERE status = 'open'
               ORDER BY updated_at ASC
               LIMIT ?""",
            (max(1, min(int(limit or 100), 500)),),
        )
        rows = await cursor.fetchall()
        for row in rows:
            result["scanned"] += 1
            existing_meta = _json_loads(row["meta_json"], {})
            if not isinstance(existing_meta, dict):
                existing_meta = {}
            if existing_meta.get("drift_semantic_reviewed") and not force:
                result["skipped"] += 1
                continue
            term = await self.get_term(row["term_id"])
            if term is None:
                # Term gone — close the dangling drift quietly.
                merged_meta = {**existing_meta, "missing_term": True}
                now_iso = _now_iso()
                await db.execute(
                    """UPDATE slang_drift_reviews
                       SET status = 'auto_dismissed', updated_at = ?, meta_json = ?
                       WHERE drift_id = ?""",
                    (now_iso, json.dumps(merged_meta, ensure_ascii=False), row["drift_id"]),
                )
                await db.commit()
                result["dismissed"] += 1
                continue
            try:
                assessment = await self._drift_reviewer.review_drift(
                    existing=term,
                    new_meaning=row["new_meaning"] or "",
                    aliases=_json_loads(row["aliases_json"], []),
                    evidence=row["evidence"] or "",
                    confidence=float(row["confidence"] or 0.0),
                    reason=row["reason"] or "",
                )
            except Exception:
                assessment = None
            if assessment is None or not assessment.reviewed or assessment.error:
                result["errors"] += 1
                continue
            merged_meta = {**existing_meta, **assessment.to_meta(), "drift_semantic_reviewed": True}
            now_iso = _now_iso()
            if assessment.verdict == "same_meaning":
                await db.execute(
                    """UPDATE slang_drift_reviews
                       SET status = 'auto_dismissed', updated_at = ?, meta_json = ?
                       WHERE drift_id = ?""",
                    (now_iso, json.dumps(merged_meta, ensure_ascii=False), row["drift_id"]),
                )
                await db.commit()
                with contextlib.suppress(Exception):
                    await self.record_revision(
                        term.term_id,
                        action="drift_dismissed",
                        after={"drift_id": row["drift_id"], "verdict": "same_meaning"},
                        reason=assessment.reason or "drift_semantic_same_meaning",
                        meta=merged_meta,
                    )
                result["dismissed"] += 1
            elif assessment.verdict == "alias_candidate":
                merged_aliases = _dedupe([*term.aliases, *_json_loads(row["aliases_json"], [])])
                if merged_aliases != term.aliases:
                    with contextlib.suppress(Exception):
                        await self.update_term(
                            term.term_id,
                            aliases=merged_aliases,
                            revision_action="drift_aliased_auto",
                            revision_reason=assessment.reason or "drift_semantic_alias",
                            revision_meta=merged_meta,
                        )
                await db.execute(
                    """UPDATE slang_drift_reviews
                       SET status = 'auto_aliased', updated_at = ?, meta_json = ?
                       WHERE drift_id = ?""",
                    (now_iso, json.dumps(merged_meta, ensure_ascii=False), row["drift_id"]),
                )
                await db.commit()
                result["aliased"] += 1
            else:
                # real_drift or unclear: keep open, stamp so we don't re-pay LLM.
                await db.execute(
                    """UPDATE slang_drift_reviews
                       SET updated_at = ?, meta_json = ?
                       WHERE drift_id = ?""",
                    (now_iso, json.dumps(merged_meta, ensure_ascii=False), row["drift_id"]),
                )
                await db.commit()
                result["kept_real_drift"] += 1
        return result

    async def age_out_open_drifts(self, *, days: int) -> int:
        """Auto-close ``open`` drift rows whose ``updated_at`` is older than ``days``.

        Returns the number of rows transitioned to ``aged_out``. ``days <= 0``
        is treated as a no-op so the caller can disable the gate without
        special-casing the call site.
        """
        if days <= 0:
            return 0
        db = self._require_db()
        cutoff = (datetime.now(TZ_SHANGHAI) - timedelta(days=int(days))).isoformat(timespec="seconds")
        cursor = await db.execute(
            """SELECT drift_id, term_id, term, meta_json
               FROM slang_drift_reviews
               WHERE status = 'open' AND updated_at < ?""",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0
        now = _now_iso()
        aged: list[tuple[str, str, dict[str, Any]]] = []
        for row in rows:
            existing_meta = _json_loads(row["meta_json"], {})
            if not isinstance(existing_meta, dict):
                existing_meta = {}
            existing_meta.update(
                {"aged_out_at": now, "aged_out_after_days": int(days), "aged_out_reason": "stale_no_human_review"},
            )
            aged.append((row["drift_id"], row["term_id"], existing_meta))
        await db.executemany(
            """UPDATE slang_drift_reviews
               SET status = 'aged_out', updated_at = ?, meta_json = ?
               WHERE drift_id = ?""",
            [(now, json.dumps(meta, ensure_ascii=False), drift_id) for drift_id, _, meta in aged],
        )
        await db.commit()
        for drift_id, term_id, meta in aged:
            with contextlib.suppress(Exception):
                await self.record_revision(
                    term_id,
                    action="drift_aged_out",
                    after={"drift_id": drift_id, "days": int(days)},
                    reason="auto-closed: open drift exceeded age-out window",
                    meta=meta,
                )
        return len(aged)

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

    async def reject_drift_review(self, drift_id: str, *, reviewer: str = "admin") -> bool:
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
                meta={"drift_id": drift.drift_id, "new_meaning": drift.new_meaning},
            )
        return await self._set_drift_status(drift_id, "rejected", {"reviewed_by": reviewer})

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
        if "cross_group_visible" in fields:
            raise ValueError(
                "cross_group_visible can only be set via set_cross_group_visibility()"
            )
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

        # Collision detection: when term or aliases change, verify no overlap
        # with other entries in the same scope+group.
        if "term" in updates or "aliases" in updates:
            check_term = str(updates["term"]).strip() if "term" in updates else term.term
            if "aliases" in updates:
                check_aliases = updates["aliases"]
                if isinstance(check_aliases, str):
                    check_aliases = [p.strip() for p in re.split(r"[,，\n]", check_aliases) if p.strip()]
                check_aliases = list(check_aliases or [])
            else:
                check_aliases = list(term.aliases)
            check_scope = str(updates.get("scope", term.scope))
            check_group = str(updates.get("group_id", term.group_id)) if check_scope != "global" else ""
            collision = await self.find_existing(
                term=check_term,
                group_id=check_group,
                scope=check_scope,
                aliases=check_aliases,
                exclude_term_ids=[term_id],
            )
            if collision is not None:
                raise SlangCollisionError(term_id, collision.term_id, collision.term)

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
        if changed and ("term" in updates or "meaning" in updates):
            updated = await self.get_term(term_id)
            if updated is not None:
                await self._attach_normalizer(
                    term_id=term_id,
                    raw_text=f"{updated.term} {updated.meaning}",
                    scope=updated.scope,
                    group_id=updated.group_id,
                )
        return changed

    async def set_cross_group_visibility(
        self,
        term_id: str,
        *,
        visible: bool,
        actor: str,
        reason: str = "",
        enabled_for_groups: list[str] | None = None,
    ) -> bool:
        """Toggle cross-group visibility. Only callable by admin."""
        db = self._require_db()
        term = await self.get_term(term_id)
        if term is None:
            return False
        before_snapshot = _term_revision_snapshot(term)
        now = _now_iso()
        enabled_by = actor if visible else ""
        enabled_at = now if visible else ""
        groups_payload = (
            [str(g).strip() for g in (enabled_for_groups or []) if str(g).strip()]
            if visible
            else []
        )
        reason_payload = reason if visible else ""
        cursor = await db.execute(
            """UPDATE slang_terms
               SET cross_group_visible = ?, cross_group_enabled_by = ?,
                   cross_group_enabled_at = ?,
                   cross_group_enabled_for_groups = ?,
                   cross_group_enabled_reason = ?,
                   updated_at = ?
               WHERE term_id = ?""",
            (
                int(visible),
                enabled_by,
                enabled_at,
                json.dumps(groups_payload, ensure_ascii=False),
                reason_payload,
                now,
                term_id,
            ),
        )
        await db.commit()
        if cursor.rowcount > 0:
            after = await self.get_term(term_id)
            await self.record_revision(
                term_id,
                action="cross_group_enable" if visible else "cross_group_disable",
                actor=actor,
                before=before_snapshot,
                after=_term_revision_snapshot(after),
                reason=reason or f"cross_group_visible={'true' if visible else 'false'}",
            )
            return True
        return False

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
            if source is None:
                continue
            if source.scope != target.scope or source.group_id != target.group_id:
                raise SlangCrossScopeMergeError(
                    target_id=target_id,
                    source_id=source.term_id,
                    target_scope=target.scope,
                    target_group_id=target.group_id,
                    source_scope=source.scope,
                    source_group_id=source.group_id,
                )
            sources.append(source)
        if not sources:
            return target
        aliases = _dedupe([
            *target.aliases,
            *(source.term for source in sources),
            *(alias for source in sources for alias in source.aliases),
        ])
        # Collision detection: merged alias set must not overlap with any
        # third-party term in the same scope+group.
        all_involved_ids = [target_id, *(s.term_id for s in sources)]
        collision = await self.find_existing(
            term=target.term,
            group_id=target.group_id,
            scope=target.scope,
            aliases=aliases,
            exclude_term_ids=all_involved_ids,
        )
        if collision is not None:
            raise SlangCollisionError(target_id, collision.term_id, collision.term)
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
        if merged is not None:
            await self._attach_normalizer(
                term_id=target_id,
                raw_text=f"{merged.term} {merged.meaning}",
                scope=merged.scope,
                group_id=merged.group_id,
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
                ORDER BY count DESC, confidence DESC, last_seen_at DESC
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

    async def get_last_extract_run_time(self) -> datetime | None:
        """Return the started_at of the most recent non-backlog, non-daily extract run."""
        db = self._require_db()
        cursor = await db.execute(
            """SELECT started_at FROM slang_extraction_runs
               WHERE status IN ('success', 'running')
                 AND (meta_json NOT LIKE '%"kind": "backlog_%' AND meta_json NOT LIKE '%"kind":"backlog_%')
                 AND (meta_json NOT LIKE '%"kind": "daily_%' AND meta_json NOT LIKE '%"kind":"daily_%')
               ORDER BY started_at DESC LIMIT 1"""
        )
        row = await cursor.fetchone()
        if not row:
            return None
        with contextlib.suppress(Exception):
            return datetime.fromisoformat(row["started_at"])
        return None

    async def mark_stale_running_runs(
        self,
        *,
        status: str = "abandoned",
        reason: str = "process restart while running",
    ) -> int:
        """Close any 'running' extraction runs left over from a prior crash/timeout."""
        db = self._require_db()
        cursor = await db.execute("SELECT run_id, started_at FROM slang_extraction_runs WHERE status = 'running'")
        rows = list(await cursor.fetchall())
        if not rows:
            return 0
        finished = datetime.now(TZ_SHANGHAI)
        for row in rows:
            started = finished
            with contextlib.suppress(Exception):
                started = datetime.fromisoformat(row["started_at"])
            duration_ms = max(0, int((finished - started).total_seconds() * 1000))
            await db.execute(
                """UPDATE slang_extraction_runs
                   SET status = ?, finished_at = ?, error = ?, duration_ms = ?
                   WHERE run_id = ?""",
                (status, finished.isoformat(timespec="seconds"), reason[:1000], duration_ms, row["run_id"]),
            )
        await db.commit()
        return len(rows)

    async def scan_global_candidates(self, *, min_groups: int = 3) -> dict[str, Any]:
        db = self._require_db()
        cursor = await db.execute(
            """SELECT * FROM slang_terms
               WHERE scope = 'group' AND status IN ('candidate', 'approved')"""
        )
        groups: dict[str, list[SlangTerm]] = {}
        for row in await cursor.fetchall():
            term = _row_to_term(row)
            groups.setdefault(normalize_term(term.term), []).append(term)
            for alias in term.aliases:
                groups.setdefault(normalize_term(alias), []).append(term)
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
            # Aggregate aliases from all per-group sources so the candidate
            # global term collides with any pre-existing global term that
            # already lists one of these surface forms.
            aggregated_aliases = _dedupe([
                alias
                for term in terms
                for alias in (term.term, *term.aliases)
            ])
            existing_global = await self.find_existing(
                term=terms[0].term,
                group_id="",
                scope="global",
                aliases=aggregated_aliases,
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
                    }, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            created += 1
        await db.commit()
        return {"ok": True, "created": created, "skipped": skipped}

    async def find_matching_terms(
        self,
        *,
        group_id: str,
        text: str,
        include_candidates: bool = True,
    ) -> list[SlangTerm]:
        if not group_id or not text:
            return []
        statuses = ("approved", "candidate") if include_candidates else ("approved",)
        placeholders = ",".join("?" for _ in statuses)
        db = self._require_db()
        cursor = await db.execute(
            f"""SELECT * FROM slang_terms
                WHERE status IN ({placeholders})
                  AND {cross_group_where()}""",
            [*statuses, group_id],
        )
        normalized_text = normalize_term(text)
        result: list[SlangTerm] = []
        for row in await cursor.fetchall():
            term = _row_to_term(row)
            candidates = [term.term, *term.aliases]
            for candidate in candidates:
                key = normalize_term(candidate)
                if len(key) >= 2 and key in normalized_text:
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
        max_indirect_terms: int | None = None,
    ) -> list[SlangTerm]:
        if not group_id:
            return []
        db = self._require_db()
        cursor = await db.execute(
            f"""SELECT * FROM slang_terms
               WHERE status = 'approved'
                 AND confidence >= ?
                 AND {cross_group_where()}""",
            (max(0.0, min(1.0, float(min_confidence or 0.0))), group_id),
        )
        terms = [_row_to_term(row) for row in await cursor.fetchall()]
        normalized_text = normalize_term(conversation_text)

        def is_direct_hit(term: SlangTerm) -> bool:
            for name in (term.term, *term.aliases):
                key = normalize_term(name)
                if len(key) >= 2 and key in normalized_text:
                    return True
            return False

        def rank(term: SlangTerm) -> tuple[int, float, int, str]:
            scope_priority = 1 if term.scope == "group" else 0
            return (scope_priority, term.confidence, term.usage_count, term.last_seen_at)

        direct_terms: list[SlangTerm] = []
        indirect_terms: list[SlangTerm] = []
        for term in terms:
            (direct_terms if is_direct_hit(term) else indirect_terms).append(term)
        direct_terms.sort(key=rank, reverse=True)
        indirect_terms.sort(key=rank, reverse=True)

        # Direct hits are always preferred and take the full budget. Indirect
        # ("group background") terms only fill the leftover slots, capped by
        # `max_indirect_terms` so a busy group's high-confidence dictionary
        # cannot drown the prompt every turn.
        max_terms = max(0, int(max_terms or 0))
        cap = max_indirect_terms if max_indirect_terms is not None else max_terms
        indirect_cap = max(0, min(int(cap), max_terms))
        merged = list(direct_terms[:max_terms])
        if len(merged) < max_terms and indirect_cap > 0:
            slots_left = max_terms - len(merged)
            merged.extend(indirect_terms[: min(slots_left, indirect_cap)])
        return merged

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
        where = ["status = 'approved'", "confidence >= ?"]
        values: list[Any] = [min_conf]
        if group_id:
            where.append(cross_group_where())
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
        terms = [_row_to_term(row) for row in await cursor.fetchall()]
        if terms or not key:
            return terms
        scope_sql = "scope = 'global'" if not group_id else cross_group_where()
        scope_values: list[Any] = [] if not group_id else [str(group_id)]
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
            names = [term.term, *term.aliases]
            meaning_similarity = _ngram_similarity(query_value, term.meaning)
            if (
                any(key in normalize_term(name) or normalize_term(name) in key for name in names)
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
        max_indirect_terms: int | None = None,
    ) -> str:
        settings = await self.load_settings()
        if max_indirect_terms is None:
            max_indirect_terms = settings.max_indirect_inject_terms
        terms = await self.get_injectable_terms(
            group_id=group_id,
            conversation_text=conversation_text,
            max_terms=max_terms,
            min_confidence=settings.min_inject_confidence,
            max_indirect_terms=max_indirect_terms,
        )
        if not terms:
            return ""
        lines = [
            "以下是当前群的黑话/约定用语。优先用于理解群聊上下文，不要为了显得懂梗而强行复述。",
        ]
        for term in terms:
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
        ai_reviewed_any = _ai_reviewed_sql_condition()
        ai_rejected = _ai_rejected_sql_condition()
        ai_kept = _ai_kept_sql_condition()
        cursor = await db.execute(
            f"SELECT COUNT(*) AS cnt FROM slang_terms WHERE status = 'approved' AND {ai_reviewed}"
        )
        ai_review_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS cnt FROM slang_terms
                WHERE status = 'approved' AND {ai_reviewed} AND NOT {human_reviewed}"""
        )
        ai_pending_review_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS cnt FROM slang_terms
                WHERE status = 'candidate' AND {ai_reviewed_any} AND {ai_kept}"""
        )
        under_observation_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS cnt FROM slang_terms
                WHERE status = 'muted' AND {ai_rejected} AND NOT {human_reviewed}"""
        )
        ai_rejected_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            f"SELECT COUNT(*) AS cnt FROM slang_terms WHERE status = 'approved' AND {human_reviewed}"
        )
        human_reviewed_count = int((await cursor.fetchone())["cnt"] or 0)
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM slang_terms WHERE status = 'candidate' AND usage_count >= 3"
        )
        eligible_backlog_count = int((await cursor.fetchone())["cnt"] or 0)
        candidate_unreviewed_count = counts.get("candidate", 0) - under_observation_count
        return {
            "candidate_count": counts.get("candidate", 0),
            "candidate_unreviewed_count": candidate_unreviewed_count,
            "approved_count": counts.get("approved", 0),
            "muted_count": counts.get("muted", 0),
            "expired_count": counts.get("expired", 0),
            "pending_count": pending_count,
            "drift_count": drift_count,
            "ai_review_count": ai_review_count,
            "ai_pending_review_count": ai_pending_review_count,
            "under_observation_count": under_observation_count,
            "ai_rejected_count": ai_rejected_count,
            "human_reviewed_count": human_reviewed_count,
            "eligible_backlog_count": eligible_backlog_count,
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
